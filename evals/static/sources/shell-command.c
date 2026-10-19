/* Synthetic command composition, static analysis only. */
typedef unsigned long size_t;
extern int snprintf(char *, size_t, const char *, ...);
extern int system(const char *);
int handle_request(const char *request) {
    char command[256];
    snprintf(command, sizeof command, "/usr/bin/printf %s", request);
    return system(command);
}
