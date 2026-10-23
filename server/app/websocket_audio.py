"""WebSocket /audio handler."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from .broadcaster import Broadcaster
from .config import Settings
from .models import Session, SessionState, DeviceInfo
from .pipeline import InsightPipeline
from .protocol import parse_packet, ProtocolError
from .recorder import Recorder
from .session_manager import SessionManager
from .storage import Storage
from .transcription_engine import TranscriptResult
from .transcription_queue import TranscriptionQueue
from .transcript_log import TranscriptLog
from .wav_writer import WAVWriter
from .whisper_engine import WhisperEngine

logger = logging.getLogger("lifelog.ws.audio")


class AudioWebSocket:
    def __init__(
        self,
        settings: Settings,
        session_manager: SessionManager,
        storage: Storage,
        broadcaster: Broadcaster,
        journal: TranscriptLog | None = None,
    ):
        self.settings = settings
        self.session_manager = session_manager
        self.storage = storage
        self.broadcaster = broadcaster
        self.journal = journal

    async def handle(self, ws: WebSocket) -> None:
        await ws.accept()
        device_id: Optional[uuid.UUID] = None
        session: Optional[Session] = None
        recorder: Optional[Recorder] = None
        tq: Optional[TranscriptionQueue] = None
        pipeline: Optional[InsightPipeline] = None
        pipeline_task: Optional[asyncio.Task] = None
        try:
            # Wait for first packet to identify device
            first = await ws.receive_bytes()
            pkt = parse_packet(first)
            device_id = pkt.device_id

            session = self.session_manager.create_session(device_id=device_id)
            self.session_manager.transition(session, SessionState.CONNECTING)
            self.session_manager.transition(session, SessionState.CONNECTED)
            self.session_manager.transition(session, SessionState.RECORDING)

            # Setup storage + recorder
            self.storage.create_session_dir(session.session_id)
            wav_path = self.storage.recording_path(session.session_id)
            wav_writer = WAVWriter(
                wav_path,
                sample_rate=self.settings.sample_rate,
                channels=self.settings.channels,
                bit_depth=self.settings.bit_depth,
            )
            recorder = Recorder(wav_writer)
            recorder.start()

            # Transcription engine + queue
            engine = WhisperEngine(
                model_name=self.settings.whisper_model,
                device=self.settings.whisper_device,
                compute_type=self.settings.whisper_compute_type,
                beam_size=self.settings.whisper_beam_size,
                vad_filter=self.settings.whisper_vad_filter,
                initial_prompt=self.settings.whisper_initial_prompt,
            )
            loop = asyncio.get_event_loop()

            # Insight pipeline: turns speech into memories/tasks via the LLM.
            pipeline = InsightPipeline(
                broadcaster=self.broadcaster,
                session_id=session.session_id,
                device_id=str(device_id),
                model=self.settings.extraction_model,
                threshold=self.settings.importance_threshold,
                min_chars=self.settings.extraction_min_chars,
                max_wait_seconds=self.settings.extraction_max_wait_seconds,
                enabled=self.settings.extraction_enabled,
            )

            def on_result(result: TranscriptResult):
                text = result.text
                if not text:
                    return
                session.transcript_segments.append(text)
                session.full_transcript = (session.full_transcript + " " + text).strip()
                # update transcript file
                self.storage.write_transcript(session.session_id, session.full_transcript)

                # Journal the finalized segment for the rolling-window
                # summarizer. This is append-only and never drained.
                if self.journal is not None:
                    seg = result.segments[0] if result.segments else None
                    self.journal.append_text(
                        text=text,
                        session_id=session.session_id,
                        device_id=str(device_id),
                        timestamp=time.time(),
                        start_ms=int((seg.start if seg else 0) * 1000),
                        end_ms=int((seg.end if seg else 0) * 1000),
                    )

                msg = {
                    "type": "transcript",
                    "session_id": session.session_id,
                    "device_id": str(device_id),
                    "sequence": session.packets_received,
                    "timestamp_ms": pkt.timestamp,
                    "text": text,
                    "is_final": True,
                }
                asyncio.run_coroutine_threadsafe(self.broadcaster.broadcast(msg), loop)
                # Feed the AI pipeline: decides if this is worth keeping and
                # turns it into a memory.
                if pipeline is not None:
                    asyncio.run_coroutine_threadsafe(pipeline.feed(text), loop)

            tq = TranscriptionQueue(
                engine=engine,
                window_bytes=self.settings.audio_window_bytes,
                on_result=on_result,
                sample_rate=self.settings.sample_rate,
            )
            await tq.start()

            # Broadcast device_connected
            await self.broadcaster.broadcast(
                {
                    "type": "device_connected",
                    "device_id": str(device_id),
                    "device_name": "xiao-esp32s3",
                    "firmware_version": "1.0.0",
                    "hardware_revision": "seeed-xiao-esp32s3-sense",
                    "protocol_version": 1,
                }
            )
            await self.broadcaster.broadcast(
                {
                    "type": "recording_started",
                    "session_id": session.session_id,
                    "device_id": str(device_id),
                    "started_at_ms": int(session.started_at.timestamp() * 1000),
                }
            )

            # Process first packet
            await self._process_packet(pkt, session, recorder, tq)

            # Background timer: flush the analysis buffer if the speaker goes
            # quiet before the character threshold is reached.
            async def _pipeline_ticker():
                while True:
                    await asyncio.sleep(2.0)
                    try:
                        await pipeline.tick()
                    except Exception:
                        logger.exception("pipeline tick failed")

            pipeline_task = asyncio.create_task(_pipeline_ticker())

            # Main loop
            while True:
                data = await ws.receive_bytes()
                try:
                    p = parse_packet(data)
                except ProtocolError as e:
                    logger.warning("invalid packet: %s", e)
                    continue
                if p.packet_type.value == 0x06:
                    break
                await self._process_packet(p, session, recorder, tq)
        except WebSocketDisconnect:
            logger.info("audio websocket disconnected")
        except Exception as e:
            logger.exception("audio handler error: %s", e)
        finally:
            # teardown
            if pipeline_task is not None:
                pipeline_task.cancel()
                try:
                    await pipeline_task
                except (asyncio.CancelledError, Exception):
                    pass
            if pipeline is not None:
                try:
                    await pipeline.close()
                except Exception:
                    logger.exception("pipeline close failed")
            if tq is not None:
                await tq.stop()
            if recorder is not None:
                try:
                    recorder.stop()
                except Exception:
                    pass
            if session is not None:
                try:
                    self.session_manager.transition(session, SessionState.FINISHING)
                except ValueError:
                    pass
                try:
                    self.session_manager.transition(session, SessionState.CLOSED)
                except ValueError:
                    session.state = SessionState.CLOSED
                # metadata
                try:
                    self.storage.write_metadata(
                        session.session_id,
                        {
                            "session_id": session.session_id,
                            "device_id": str(device_id) if device_id else None,
                            "started_at": session.started_at.isoformat(),
                            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                            "bytes_received": session.bytes_received,
                            "packets_received": session.packets_received,
                            "words": len(session.full_transcript.split()),
                            "characters": len(session.full_transcript),
                        },
                    )
                except Exception as e:
                    logger.exception("metadata write failed: %s", e)
                await self.broadcaster.broadcast(
                    {
                        "type": "recording_finished",
                        "session_id": session.session_id,
                        "device_id": str(device_id) if device_id else None,
                        "duration_ms": int(((session.ended_at or session.started_at) - session.started_at).total_seconds() * 1000),
                    }
                )
            try:
                await ws.close()
            except Exception:
                pass

    async def _process_packet(self, pkt, session, recorder, tq):
        session.packets_received += 1
        session.bytes_received += pkt.payload_length
        session.last_sequence = pkt.sequence
        if pkt.packet_type.value == 0x01:  # AUDIO
            recorder.write(pkt.payload)
            await tq.enqueue(pkt.payload)
        elif pkt.packet_type.value == 0x02:  # HEARTBEAT
            pass
        elif pkt.packet_type.value == 0x03:  # DEVICE_INFO
            pass