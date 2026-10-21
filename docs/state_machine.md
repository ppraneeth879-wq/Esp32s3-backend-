# Audio Session State Machine

IDLE -> CONNECTING -> CONNECTED -> RECORDING <-> TRANSCRIBING -> FINISHING -> CLOSED -> IDLE

Each state has entry/exit actions defined in session_manager (backend) and
StateMachine (firmware). Illegal transitions raise ValueError.