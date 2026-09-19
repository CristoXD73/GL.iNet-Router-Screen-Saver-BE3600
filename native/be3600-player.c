/*
 * be3600-player -- native player for .bea animations (BEA1 and BEA2) on the
 * GL.iNet GL-BE3600's front display. A drop-in replacement for
 * be3600-player.lua that keeps exact time and uses almost no CPU: the Lua player
 * starts a separate /bin/usleep process for every frame, this one sleeps until an
 * absolute deadline with clock_nanosleep(), so timing errors do not add up.
 *
 *   be3600-player [file.bea]      default: /etc/be3600-screen/active.bea
 *
 * Test hooks (unused in normal operation), same as the Lua player:
 *   BE3600_FB     framebuffer path (point it at an ordinary file to test on a PC)
 *   BE3600_LOOPS  exit after this many complete passes instead of looping forever
 *
 * The file is loaded and completely validated before anything is drawn, so the
 * playback loop never reads outside the data. Format: docs/BEA-FORMAT.md.
 *
 * Build: native/build.sh (cross-compiles a static aarch64 binary), or on a PC for
 * testing:  cc -O2 -o be3600-player native/be3600-player.c
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define FRAME_BYTES 43168u
#define HEADER_BYTES 12u
#define MAX_FILE (256u * 1024u * 1024u)

enum { KIND_FULL = 0, KIND_DELTA = 1, KIND_HOLD = 2 };

struct record {
    uint16_t run;      /* ticks this picture stays on screen */
    uint8_t kind;
    uint32_t off;      /* payload offset in the loaded file */
    uint32_t len;      /* payload length */
};

static volatile sig_atomic_t stop_requested;

static void on_signal(int sig)
{
    (void)sig;
    stop_requested = 1;
}

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int die(const char *what, const char *why)
{
    fprintf(stderr, "be3600-player: %s: %s\n", what, why);
    return 1;
}

static uint8_t *load_file(const char *path, size_t *size)
{
    FILE *f = fopen(path, "rb");
    struct stat st;
    uint8_t *buf;

    if (!f) { die(path, strerror(errno)); return NULL; }
    if (fstat(fileno(f), &st) != 0 || st.st_size < (off_t)HEADER_BYTES || (uint64_t)st.st_size > MAX_FILE) {
        fclose(f);
        die(path, "not a usable .bea file (size)");
        return NULL;
    }
    buf = malloc((size_t)st.st_size);
    if (!buf || fread(buf, 1, (size_t)st.st_size, f) != (size_t)st.st_size) {
        fclose(f);
        free(buf);
        die(path, "could not read the file");
        return NULL;
    }
    fclose(f);
    *size = (size_t)st.st_size;
    return buf;
}

/* Parse and validate. Returns the record table (caller frees) or NULL. */
static struct record *index_file(const uint8_t *buf, size_t size, int *is_v2, unsigned *fps, unsigned *count)
{
    struct record *recs;
    size_t pos = HEADER_BYTES;
    unsigned i, n;

    if (memcmp(buf, "BEA1", 4) == 0) *is_v2 = 0;
    else if (memcmp(buf, "BEA2", 4) == 0) *is_v2 = 1;
    else { die("animation", "bad magic (expected BEA1 or BEA2)"); return NULL; }

    *fps = rd16(buf + 4);
    n = rd16(buf + 6);
    if (rd32(buf + 8) != FRAME_BYTES) { die("animation", "frame size is not 43168 bytes"); return NULL; }
    if (*fps < 1 || *fps > 24) { die("animation", "fps must be 1 to 24"); return NULL; }
    if (n < 1) { die("animation", "no frames"); return NULL; }

    recs = calloc(n, sizeof *recs);
    if (!recs) { die("animation", "out of memory"); return NULL; }

    for (i = 0; i < n; i++) {
        struct record *r = &recs[i];

        if (!*is_v2) {
            if (pos + 2 + FRAME_BYTES > size) goto truncated;
            r->run = rd16(buf + pos);
            r->kind = KIND_FULL;
            r->off = (uint32_t)(pos + 2);
            r->len = FRAME_BYTES;
            pos += 2 + FRAME_BYTES;
            continue;
        }

        if (pos + 7 > size) goto truncated;
        r->run = rd16(buf + pos);
        r->kind = buf[pos + 2];
        r->len = rd32(buf + pos + 3);
        pos += 7;
        if (r->len > size - pos) goto truncated;
        r->off = (uint32_t)pos;

        if (i == 0 && r->kind != KIND_FULL) { free(recs); die("animation", "the first record must be a full frame"); return NULL; }

        if (r->kind == KIND_FULL) {
            if (r->len != FRAME_BYTES) { free(recs); die("animation", "a full frame must be 43168 bytes"); return NULL; }
        } else if (r->kind == KIND_HOLD) {
            if (r->len != 0) { free(recs); die("animation", "a hold record has no data"); return NULL; }
        } else if (r->kind == KIND_DELTA) {
            const uint8_t *p = buf + r->off;
            size_t at = 2;
            unsigned s, spans;

            if (r->len < 2) { free(recs); die("animation", "bad delta record"); return NULL; }
            spans = rd16(p);
            for (s = 0; s < spans; s++) {
                uint32_t off, len;
                if (at + 6 > r->len) { free(recs); die("animation", "a delta record is truncated"); return NULL; }
                off = rd32(p + at);
                len = rd16(p + at + 4);
                at += 6;
                if (len < 1 || (uint64_t)off + len > FRAME_BYTES || at + len > r->len) {
                    free(recs); die("animation", "a delta span lies outside the frame"); return NULL;
                }
                at += len;
            }
            if (at != r->len) { free(recs); die("animation", "a delta record's length does not match its spans"); return NULL; }
        } else {
            free(recs); die("animation", "unknown record kind"); return NULL;
        }
        pos += r->len;
    }

    if (pos != size) { free(recs); die("animation", "unexpected data after the last record"); return NULL; }
    *count = n;
    return recs;

truncated:
    free(recs);
    die("animation", "the file is cut off");
    return NULL;
}

static int write_all(int fd, const uint8_t *p, size_t len, off_t at)
{
    while (len > 0) {
        ssize_t w = pwrite(fd, p, len, at);
        if (w < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        p += w;
        at += w;
        len -= (size_t)w;
    }
    return 0;
}

static void ts_add_ns(struct timespec *t, long long ns)
{
    t->tv_sec += (time_t)(ns / 1000000000LL);
    t->tv_nsec += (long)(ns % 1000000000LL);
    if (t->tv_nsec >= 1000000000L) { t->tv_sec++; t->tv_nsec -= 1000000000L; }
}

static long long ts_diff_ns(const struct timespec *a, const struct timespec *b)
{
    return (long long)(a->tv_sec - b->tv_sec) * 1000000000LL + (a->tv_nsec - b->tv_nsec);
}

int main(int argc, char **argv)
{
    const char *path = argc > 1 ? argv[1] : "/etc/be3600-screen/active.bea";
    const char *fbpath = getenv("BE3600_FB");
    const char *loops_env = getenv("BE3600_LOOPS");
    long max_loops = loops_env && *loops_env ? atol(loops_env) : 0;
    long loops = 0;
    size_t size = 0;
    uint8_t *buf;
    struct record *recs;
    int is_v2 = 0, fd;
    unsigned fps = 0, n = 0, i;
    long long tick_ns;
    struct timespec next, now;
    struct sigaction sa;

    if (argc > 1 && strcmp(argv[1], "--version") == 0) {
        puts("be3600-player 1 (BEA1, BEA2)");
        return 0;
    }

    buf = load_file(path, &size);
    if (!buf) return 1;
    recs = index_file(buf, size, &is_v2, &fps, &n);
    if (!recs) return 1;

    fd = open(fbpath ? fbpath : "/dev/fb0", O_WRONLY | (fbpath ? O_CREAT : 0), 0644);
    if (fd < 0) return die(fbpath ? fbpath : "/dev/fb0", strerror(errno));

    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal;             /* no SA_RESTART: a signal must interrupt the sleep */
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    tick_ns = 1000000000LL / (long long)fps;
    clock_gettime(CLOCK_MONOTONIC, &next);

    while (!stop_requested) {
        for (i = 0; i < n && !stop_requested; i++) {
            const struct record *r = &recs[i];
            const uint8_t *payload = buf + r->off;

            if (r->kind == KIND_FULL) {
                if (write_all(fd, payload, FRAME_BYTES, 0) != 0) return die("framebuffer", strerror(errno));
            } else if (r->kind == KIND_DELTA) {
                unsigned spans = rd16(payload), s;
                size_t at = 2;
                for (s = 0; s < spans; s++) {
                    uint32_t off = rd32(payload + at);
                    uint16_t len = rd16(payload + at + 4);
                    if (write_all(fd, payload + at + 6, len, (off_t)off) != 0) return die("framebuffer", strerror(errno));
                    at += 6u + len;
                }
            }

            /* Sleep until an absolute deadline so errors never accumulate. */
            ts_add_ns(&next, tick_ns * (long long)r->run);
            clock_gettime(CLOCK_MONOTONIC, &now);
            if (ts_diff_ns(&now, &next) > 1000000000LL) {
                next = now;                /* far behind (e.g. the system was stalled): don't burst */
            } else {
                while (!stop_requested && clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL) == EINTR) {
                    if (stop_requested) break;
                }
            }
        }

        loops++;
        if (max_loops > 0 && loops >= max_loops) break;
    }

    close(fd);
    free(recs);
    free(buf);
    return 0;
}
