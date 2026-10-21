#include "Logger.h"
#include <stdio.h>

void Logger::log(LogLevel level, const char* tag, const char* msg) {
    const char* lvl = "INFO";
    if (level == LOG_DEBUG) lvl = "DEBUG";
    else if (level == LOG_WARNING) lvl = "WARN";
    else if (level == LOG_ERROR) lvl = "ERROR";
    Serial.printf("[%s] %s: %s\n", lvl, tag, msg);
}

void Logger::debug(const char* tag, const char* msg){ log(LOG_DEBUG, tag, msg); }
void Logger::info(const char* tag, const char* msg){ log(LOG_INFO, tag, msg); }
void Logger::warn(const char* tag, const char* msg){ log(LOG_WARNING, tag, msg); }
void Logger::error(const char* tag, const char* msg){ log(LOG_ERROR, tag, msg); }