# Generates a test speech WAV using the Windows SAPI TTS engine.
# The result is used to exercise the full pipeline (Whisper -> LLM -> memory/task).
#
#   powershell -ExecutionPolicy Bypass -File tests\make_speech.ps1
#   python tests\resample.py tests\speech_test.wav tests\speech16.wav
#
param(
    [string]$OutFile = "$PSScriptRoot\speech_test.wav",
    [string]$Text = "Okay so I need to remember to email Sarah back about the invoice tomorrow morning. And I have to call the dentist to reschedule my appointment for next week. My wife Priya's birthday is on the fourteenth of March."
)

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = -1
$synth.SetOutputToWaveFile($OutFile)
$synth.Speak($Text)
$synth.Dispose()

Write-Host "Generated $OutFile"
Get-Item $OutFile | Select-Object Length
