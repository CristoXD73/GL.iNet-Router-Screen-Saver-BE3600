/*
 * be3600-player -- native player for .bea animations (BEA1 and BEA2) on the
 * GL.iNet GL-BE3600's front display. A drop-in replacement for
 * be3600-player.lua that keeps exact time and uses almost no CPU: the Lua player
 * starts a separate /bin/usleep process for every frame, this one sleeps until an
 * absolute deadline with clock_nanosleep(), so timing errors do not add up.
 *
 *   be3600-player [--fade MS] [file.bea]   default file: /etc/be3600-screen/active.bea
 *
 * --fade MS crossfades from whatever is on the screen now into the animation's
 * first frame over about MS milliseconds (1..5000), instead of cutting to it
 * instantly. Used when switching between saved animations.
 *
 * Test hooks (unused in normal operation; they work only when BE3600_TESTING is set),
 * same as the Lua player:
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

/* Mixes one RGB565 pixel toward another. weight is 0 (all "from") to 256
 * (all "to"); exact at both ends so a full crossfade begins and ends on the
 * exact original pixels, with no rounding drift. */
static uint16_t mix565(uint16_t from, uint16_t to, unsigned weight)
{
    unsigned r0 = (from >> 11) & 31, g0 = (from >> 5) & 63, b0 = from & 31;
    unsigned r1 = (to   >> 11) & 31, g1 = (to   >> 5) & 63, b1 = to   & 31;
    unsigned r = (r0 * (256 - weight) + r1 * weight) >> 8;
    unsigned g = (g0 * (256 - weight) + g1 * weight) >> 8;
    unsigned b = (b0 * (256 - weight) + b1 * weight) >> 8;

    return (uint16_t)((r << 11) | (g << 5) | b);
}

/* Crossfades from whatever picture is currently on the screen to `target`
 * over about `ms` milliseconds, landing exactly on `target`. Falls back to
 * an instant cut (returns immediately, drawing nothing) if the screen can't
 * be read back to fade from. */
static void fade_in(int fd, const uint8_t *target, unsigned ms)
{
    static uint8_t from[FRAME_BYTES], mixed[FRAME_BYTES];
    unsigned steps, step, i;
    struct timespec next;
    long long step_ns;

    if (pread(fd, from, FRAME_BYTES, 0) != (ssize_t)FRAME_BYTES)
        return;

    steps = ms / 30;
    if (steps < 2) steps = 2;
    if (steps > 40) steps = 40;
    step_ns = (long long)ms * 1000000LL / (long long)steps;

    clock_gettime(CLOCK_MONOTONIC, &next);

    for (step = 1; step <= steps && !stop_requested; step++) {
        unsigned weight = 256u * step / steps;

        for (i = 0; i < FRAME_BYTES; i += 2) {
            uint16_t a = (uint16_t)(from[i] | (from[i + 1] << 8));
            uint16_t b = (uint16_t)(target[i] | (target[i + 1] << 8));
            uint16_t m = mix565(a, b, weight);

            mixed[i]     = (uint8_t)(m & 255);
            mixed[i + 1] = (uint8_t)(m >> 8);
        }

        if (write_all(fd, mixed, FRAME_BYTES, 0) != 0)
            return;

        ts_add_ns(&next, step_ns);

        while (!stop_requested && clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL) == EINTR) {
            if (stop_requested) break;
        }
    }
}

int main(int argc, char **argv)
{
    const char *path = "/etc/be3600-screen/active.bea";
    unsigned fade_ms = 0;
    int ai;
    /* The two test hooks work only when BE3600_TESTING is set, so nothing in a normal
     * environment can point the player at another file or stop it early. */
    const int testing = getenv("BE3600_TESTING") != NULL;
    const char *fbpath = testing ? getenv("BE3600_FB") : NULL;
    const char *loops_env = testing ? getenv("BE3600_LOOPS") : NULL;
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

    for (ai = 1; ai < argc; ai++) {
        if (strcmp(argv[ai], "--version") == 0) {
            puts("be3600-player 2 (BEA1, BEA2, --fade)");
            return 0;
        } else if (strcmp(argv[ai], "--fade") == 0 && ai + 1 < argc) {
            long v = atol(argv[ai + 1]);
            fade_ms = (v > 0 && v <= 5000) ? (unsigned)v : 0;
            ai++;
        } else {
            path = argv[ai];
        }
    }

    buf = load_file(path, &size);
    if (!buf) return 1;
    recs = index_file(buf, size, &is_v2, &fps, &n);
    if (!recs) return 1;

    /* O_RDWR (not O_WRONLY): a fade needs to read back what is on the screen
     * now before blending into the first frame. */
    fd = open(fbpath ? fbpath : "/dev/fb0", O_RDWR | (fbpath ? O_CREAT : 0), 0644);
    if (fd < 0) return die(fbpath ? fbpath : "/dev/fb0", strerror(errno));

    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal;             /* no SA_RESTART: a signal must interrupt the sleep */
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    if (fade_ms > 0)
        fade_in(fd, buf + recs[0].off, fade_ms);

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
