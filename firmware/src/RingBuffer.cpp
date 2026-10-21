#include "RingBuffer.h"

RingBuffer::RingBuffer(size_t capacity) : _cap(capacity), _head(0), _tail(0), _count(0) {
    _buf = (uint8_t*)malloc(capacity);
}

RingBuffer::~RingBuffer() { if (_buf) free(_buf); }

size_t RingBuffer::write(const uint8_t* data, size_t len) {
    size_t written = 0;
    for (size_t i = 0; i < len; i++) {
        if (_count >= _cap) break;
        _buf[_head] = data[i];
        _head = (_head + 1) % _cap;
        _count++;
        written++;
    }
    return written;
}

size_t RingBuffer::read(uint8_t* out, size_t len) {
    size_t r = 0;
    for (size_t i = 0; i < len; i++) {
        if (_count == 0) break;
        out[i] = _buf[_tail];
        _tail = (_tail + 1) % _cap;
        _count--;
        r++;
    }
    return r;
}

size_t RingBuffer::available() const { return _count; }
size_t RingBuffer::freeSpace() const { return _cap - _count; }
void RingBuffer::clear() { _head = _tail = _count = 0; }