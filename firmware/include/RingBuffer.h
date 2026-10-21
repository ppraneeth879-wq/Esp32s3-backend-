#pragma once
#include <Arduino.h>

class RingBuffer {
public:
    RingBuffer(size_t capacity);
    ~RingBuffer();
    size_t write(const uint8_t* data, size_t len);
    size_t read(uint8_t* out, size_t len);
    size_t available() const;
    size_t freeSpace() const;
    void clear();
private:
    uint8_t* _buf;
    size_t _cap;
    volatile size_t _head;
    volatile size_t _tail;
    volatile size_t _count;
};