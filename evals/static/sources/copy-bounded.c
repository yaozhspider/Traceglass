/* Matched negative fixture. Bounds cover the actual copy and consumer. */
typedef unsigned long size_t;
extern void *memcpy(void *, const void *, size_t);
extern void consume_bytes(const char *, size_t);
int handle_request(const char *request, size_t length) {
    char buffer[32];
    if (length > sizeof buffer) return -1;
    memcpy(buffer, request, length);
    consume_bytes(buffer, length);
    return 0;
}
