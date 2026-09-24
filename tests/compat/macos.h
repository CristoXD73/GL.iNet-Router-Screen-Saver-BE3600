/* Test builds on macOS only (tests/run.sh passes -include tests/compat/macos.h):
 * what Linux has and macOS does not. Never part of the router build. */
#ifndef BE3600_COMPAT_MACOS_H
#define BE3600_COMPAT_MACOS_H

#include <poll.h>
#include <signal.h>
#include <time.h>

/* ppoll() with no signal mask, which is how the player calls it. Rounded up to a
 * whole millisecond so a short wait never turns into a busy loop. */
static inline int ppoll(struct pollfd *fds, nfds_t n, const struct timespec *ts, const sigset_t *mask)
{
    (void)mask;
    int ms = -1;
    if (ts) {
        long long t = (long long)ts->tv_sec * 1000 + (ts->tv_nsec + 999999) / 1000000;
        ms = t > 2147483647LL ? 2147483647 : (int)t;
    }
    return poll(fds, n, ms);
}

#endif
