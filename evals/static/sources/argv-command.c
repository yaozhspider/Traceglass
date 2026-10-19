/* Matched negative: fixed executable, fixed format, separate data argument. */
extern int execv(const char *, char *const []);
int handle_request(const char *request) {
    char *arguments[] = {"printf", "%s", (char *)request, (char *)0};
    return execv("/usr/bin/printf", arguments);
}
