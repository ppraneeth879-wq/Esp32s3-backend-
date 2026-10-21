#pragma once
#include <Arduino.h>

enum LogLevel { LOG_DEBUG, LOG_INFO, LOG_WARNING, LOG_ERROR };

class Logger {
public:
    static void log(LogLevel level, const char* tag, const char* msg);
    static void debug(const char* tag, const char* msg);
    static void info(const char* tag, const char* msg);
    static void warn(const char* tag, const char* msg);
    static void error(const char* tag, const char* msg);
};