/*
 * be3600-player -- native player for .bea animations (BEA1 and BEA2) on the
 * GL.iNet GL-BE3600's front display. A drop-in replacement for
 * be3600-player.lua that keeps exact time and uses almost no CPU: the Lua player
 * starts a separate /bin/usleep process for every frame, this one sleeps until an
 * absolute deadline, so timing errors do not add up.
 *
 *   be3600-player [options] [file.bea]     default file: /etc/be3600-screen/active.bea
 *
 *   --fade MS       crossfade from what is on the screen now into the first frame
 *                   (1..5000 ms) instead of cutting to it
 *   --gestures      also read the touchscreen and handle it here, in this one process,
 *                   so no touch is ever lost between helper programs:
 *                     swipe along the strip   the picture follows the finger and slides to
 *                                             the next / previous saved animation
 *                     tap                     slide to the next saved animation
 *                     double-tap, or a swipe across the strip
 *                                             leave: exit code 10 (the supervisor then
 *                                             shows the normal screen again)
 *   --touch DEV     touchscreen device (default: $TOUCH_DEVICE, else the input device whose
 *                   name contains "touch")
 *   --lib DIR       saved animations (default /etc/be3600-screen/animations)
 *   --slide MS      how long a slide takes (default 280)
 *   --double-tap MS second tap within this long after the first is a double-tap (default 300)
 *   --swipe-invert  swap which swipe direction means "next"
 *   --long-axis X|Y which touchscreen axis runs along the strip (default: Y)
 *   --touch-info    print the touchscreen device and its coordinate ranges, then exit
 *
 * Exit codes: 0 stopped by a signal (or finished), 1 error, 10 the user dismissed the
 * animation, 11 the touchscreen could not be used (so the caller can fall back).
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
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define FRAME_BYTES 43168u
#define HEADER_BYTES 12u
#define MAX_FILE (256u * 1024u * 1024u)
#define ROWS 284                /* the display is 76 wide and 284 tall; the strip you see is */
#define ROW_BYTES 152u          /* that turned on its side, so "along the strip" is down the rows */

enum { KIND_FULL = 0, KIND_DELTA = 1, KIND_HOLD = 2 };

struct record {
    uint16_t run;      /* ticks this picture stays on screen */
    uint8_t kind;
    uint32_t off;      /* payload offset in the loaded file */
    uint32_t len;      /* payload length */
};

struct anim {
    uint8_t *buf;
    size_t size;
    struct record *recs;
    unsigned n, fps;
    int v2;
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

static void note(const char *fmt, const char *a, int b)
{
    fprintf(stderr, "be3600-player: ");
    fprintf(stderr, fmt, a, b);
    fputc('\n', stderr);
    fflush(stderr);
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

static int anim_load(const char *path, struct anim *a)
{
    memset(a, 0, sizeof *a);
    a->buf = load_file(path, &a->size);
    if (!a->buf) return -1;
    a->recs = index_file(a->buf, a->size, &a->v2, &a->fps, &a->n);
    if (!a->recs) { free(a->buf); a->buf = NULL; return -1; }
    return 0;
}

static void anim_free(struct anim *a)
{
    free(a->recs);
    free(a->buf);
    memset(a, 0, sizeof *a);
}

/* Reads just the first picture of a saved animation (for the next one sliding in);
 * the whole file is loaded and checked only if the switch really happens. */
static int read_first_frame(const char *path, uint8_t *dst)
{
    uint8_t hdr[HEADER_BYTES + 7];
    FILE *f = fopen(path, "rb");
    int ok = 0;
    long at = -1;

    if (!f) return -1;
    if (fread(hdr, 1, sizeof hdr, f) == sizeof hdr && rd32(hdr + 8) == FRAME_BYTES) {
        if (memcmp(hdr, "BEA1", 4) == 0)
            at = (long)HEADER_BYTES + 2;
        else if (memcmp(hdr, "BEA2", 4) == 0 && hdr[HEADER_BYTES + 2] == KIND_FULL &&
                 rd32(hdr + HEADER_BYTES + 3) == FRAME_BYTES)
            at = (long)HEADER_BYTES + 7;
    }
    if (at >= 0 && fseek(f, at, SEEK_SET) == 0 && fread(dst, 1, FRAME_BYTES, f) == FRAME_BYTES)
        ok = 1;
    fclose(f);
    return ok ? 0 : -1;
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

static long long mono_ns(void)
{
    struct timespec t;

    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000000000LL + t.tv_nsec;
}

static long long mono_ms(void) { return mono_ns() / 1000000LL; }

/* Sleeps until the absolute time target_ns, or until fd (if >= 0) has something to read.
 * Returns 1 = fd readable, 0 = time reached, -1 = a stop was requested, -2 = fd broke. */
static int wait_ready(long long target_ns, int fd)
{
    while (!stop_requested) {
        struct pollfd p;
        struct timespec ts;
        long long d = target_ns - mono_ns();
        int r;

        if (d < 0) d = 0;
        ts.tv_sec = (time_t)(d / 1000000000LL);
        ts.tv_nsec = (long)(d % 1000000000LL);
        p.fd = fd;
        p.events = POLLIN;
        p.revents = 0;
        r = ppoll(&p, 1, &ts, NULL);       /* a negative fd is ignored by poll */
        if (r > 0) {
            if (p.revents & POLLIN) return 1;
            return -2;                      /* POLLERR / POLLHUP: the device went away */
        }
        if (r == 0) return 0;
        if (errno != EINTR) return -2;
    }
    return -1;
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
    long long next, step_ns;

    if (pread(fd, from, FRAME_BYTES, 0) != (ssize_t)FRAME_BYTES)
        return;

    steps = ms / 30;
    if (steps < 2) steps = 2;
    if (steps > 40) steps = 40;
    step_ns = (long long)ms * 1000000LL / (long long)steps;

    next = mono_ns();

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

        next += step_ns;
        wait_ready(next, -1);
    }
}

/* ------------------------------------------------------------------------
 * Player state
 * ------------------------------------------------------------------------ */

static int fb;                          /* the framebuffer */
static const char *active_path;         /* the file being played (rewritten on a switch) */
static struct anim cur;                 /* what is playing */
static unsigned rec_idx;                /* the next record to show */
static uint8_t shadow[FRAME_BYTES];     /* exactly what is on the screen now */
static uint8_t comp[FRAME_BYTES];       /* a slide frame being put together */
static uint8_t black[FRAME_BYTES];      /* what slides in when there is nothing to slide to */

static int apply_record(const struct anim *a, unsigned i)
{
    const struct record *r = &a->recs[i];
    const uint8_t *payload = a->buf + r->off;

    if (r->kind == KIND_FULL) {
        memcpy(shadow, payload, FRAME_BYTES);
        if (write_all(fb, shadow, FRAME_BYTES, 0) != 0) return -1;
    } else if (r->kind == KIND_DELTA) {
        unsigned spans = rd16(payload), s;
        size_t at = 2;

        for (s = 0; s < spans; s++) {
            uint32_t off = rd32(payload + at);
            uint16_t len = rd16(payload + at + 4);

            memcpy(shadow + off, payload + at + 6, len);
            if (write_all(fb, payload + at + 6, len, (off_t)off) != 0) return -1;
            at += 6u + len;
        }
    }
    return 0;
}

/* ------------------------------------------------------------------------
 * Sliding
 *
 * off > 0: the current picture has moved that many rows toward "next" and the
 * other picture is entering behind it; off < 0 the same the other way. |off| is
 * at most ROWS. The strip you look at is the display turned on its side, and its
 * left-right is the display's rows running bottom to top, which is why row r of
 * the result comes from row r - off.
 * ------------------------------------------------------------------------ */

static void compose(uint8_t *dst, const uint8_t *from, const uint8_t *other, int off)
{
    int r;

    if (off > ROWS) off = ROWS;
    if (off < -ROWS) off = -ROWS;

    for (r = 0; r < ROWS; r++) {
        int s = r - off;
        const uint8_t *src;

        if (off >= 0) src = s >= 0 ? from + (size_t)s * ROW_BYTES : other + (size_t)(s + ROWS) * ROW_BYTES;
        else          src = s < ROWS ? from + (size_t)s * ROW_BYTES : other + (size_t)(s - ROWS) * ROW_BYTES;
        memcpy(dst + (size_t)r * ROW_BYTES, src, ROW_BYTES);
    }
}

static int draw_slide(const uint8_t *other, int off)
{
    compose(comp, shadow, other, off);
    return write_all(fb, comp, FRAME_BYTES, 0);
}

/* Moves from offset `from` to offset `to` over `ms`, easing out, then lands exactly. */
static void slide_between(const uint8_t *other, int from, int to, unsigned ms)
{
    long long start = mono_ns(), total = (long long)ms * 1000000LL, next;
    int last = from + 1, frames = 0;

    if (total < 1) total = 1;
    next = start;

    while (!stop_requested) {
        long long el = mono_ns() - start;
        double t = (double)el / (double)total, e;
        int off;

        if (t >= 1.0) break;
        e = 1.0 - (1.0 - t) * (1.0 - t) * (1.0 - t);
        off = from + (int)((double)(to - from) * e + ((to - from) >= 0 ? 0.5 : -0.5));
        if (off != last) {
            if (draw_slide(other, off) != 0) break;
            last = off;
            frames++;
        }
        next += 16000000LL;                 /* about 60 pictures a second */
        wait_ready(next, -1);
    }
    draw_slide(other, to);
    fprintf(stderr, "be3600-player: slide of %d rows: %d pictures in %d ms\n", abs(to - from), frames + 1, (int)((mono_ns() - start) / 1000000LL));
    fflush(stderr);
}

/* ------------------------------------------------------------------------
 * The saved animations (the "library") and the neighbours of the current one
 * ------------------------------------------------------------------------ */

static const char *lib_dir = "/etc/be3600-screen/animations";
static char **lib_names;
static int lib_n, lib_cur = -1, lib_ready;

struct neighbour {
    int tried, idx;
    uint8_t frame[FRAME_BYTES];
};
static struct neighbour nb[2];          /* [0] = next, [1] = previous */

static int cmp_names(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }

static void lib_path(int j, char *out, size_t cap)
{
    snprintf(out, cap, "%s/%s", lib_dir, lib_names[j]);
}

static int file_is(const char *path, const uint8_t *buf, size_t size)
{
    struct stat st;
    uint8_t *tmp;
    FILE *f;
    int same = 0;

    if (stat(path, &st) != 0 || (size_t)st.st_size != size) return 0;
    f = fopen(path, "rb");
    if (!f) return 0;
    tmp = malloc(size);
    if (tmp && fread(tmp, 1, size, f) == size && memcmp(tmp, buf, size) == 0) same = 1;
    free(tmp);
    fclose(f);
    return same;
}

/* Lists the library (sorted by name, the same order the supervisor uses) and finds
 * which entry is playing. Done on the first touch, so starting up stays fast. */
static void lib_init(void)
{
    DIR *d;
    struct dirent *e;
    int cap = 0, i;

    if (lib_ready) return;
    lib_ready = 1;

    d = opendir(lib_dir);
    if (!d) return;
    while ((e = readdir(d)) != NULL) {
        size_t l = strlen(e->d_name);

        if (l < 5 || strcmp(e->d_name + l - 4, ".bea") != 0) continue;
        if (lib_n == cap) {
            char **grown = realloc(lib_names, (size_t)(cap ? cap * 2 : 8) * sizeof *grown);

            if (!grown) break;
            lib_names = grown;
            cap = cap ? cap * 2 : 8;
        }
        lib_names[lib_n] = strdup(e->d_name);
        if (lib_names[lib_n]) lib_n++;
    }
    closedir(d);
    if (lib_n > 1) qsort(lib_names, (size_t)lib_n, sizeof *lib_names, cmp_names);

    for (i = 0; i < lib_n; i++) {
        char p[1024];

        lib_path(i, p, sizeof p);
        if (file_is(p, cur.buf, cur.size)) { lib_cur = i; break; }
    }
}

/* The picture that would slide in for direction dir (+1 next, -1 previous), or NULL if
 * there is no other animation to go to. */
static struct neighbour *neighbour_for(int dir)
{
    struct neighbour *n = &nb[dir > 0 ? 0 : 1];
    int step;

    lib_init();
    if (n->tried) return n->idx >= 0 ? n : NULL;
    n->tried = 1;
    n->idx = -1;

    for (step = 1; step <= lib_n; step++) {
        int j;
        char p[1024];

        if (lib_cur >= 0) j = ((lib_cur + dir * step) % lib_n + lib_n) % lib_n;
        else j = dir > 0 ? step - 1 : lib_n - step;
        if (j == lib_cur) continue;
        lib_path(j, p, sizeof p);
        if (read_first_frame(p, n->frame) == 0) { n->idx = j; return n; }
    }
    return NULL;
}

/* Writes the playing animation over the active file so it is what plays after a restart.
 * Done by a short-lived child process: writing megabytes to the router's flash can take a
 * second, and the touchscreen must not go unanswered meanwhile. The rename is atomic, so
 * a stop half-way leaves the old file intact. */
static void save_active(void)
{
    char tmp[1100];
    int fd;
    pid_t child = fork();

    if (child > 0) return;                 /* the parent carries on (SIGCHLD is ignored, so no zombie) */
    snprintf(tmp, sizeof tmp, "%s.new", active_path);
    fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        if (write_all(fd, cur.buf, cur.size, 0) != 0) { close(fd); unlink(tmp); }
        else {
            close(fd);
            if (rename(tmp, active_path) != 0) unlink(tmp);
        }
    }
    if (child == 0) _exit(0);              /* a child leaves here; if fork failed the parent simply did it itself */
}

/* The other picture is now fully on screen: make its animation the one playing. */
static void complete_switch(struct neighbour *n)
{
    struct anim next;
    char p[1024];
    long long t0 = mono_ms();

    lib_path(n->idx, p, sizeof p);
    if (anim_load(p, &next) != 0) {
        note("could not switch to %s (%d)", lib_names[n->idx], 0);
        apply_record(&cur, rec_idx > 0 ? rec_idx - 1 : 0);
        write_all(fb, shadow, FRAME_BYTES, 0);
        return;
    }
    memcpy(shadow, n->frame, FRAME_BYTES);
    anim_free(&cur);
    cur = next;
    rec_idx = 0;
    lib_cur = n->idx;
    nb[0].tried = nb[1].tried = 0;
    save_active();
    note("switched to %s (loaded in %d ms)", lib_names[lib_cur], (int)(mono_ms() - t0));
}

/* ------------------------------------------------------------------------
 * The touchscreen
 * ------------------------------------------------------------------------ */

#define SLOP_PX        12       /* a finger moves this much and it is a drag, not a tap */
#define TAP_MAX_MS     450      /* longer than this it is a press, not a tap */
#define COMMIT_PX      64       /* dragged this far, the slide completes on release */
#define FLING_MIN_PX   14       /* ...or this far, if it was a quick flick */
#define FLING_SPEED    0.45     /* pixels per millisecond that counts as a flick */
#define EXIT_PX        28       /* a swipe this far across the strip leaves the animation */
#define REDRAW_MS      12

static int raw_x, raw_y, have_x, have_y;   /* the finger's latest coordinates */
static int tfd = -1, ev_clock;               /* ev_clock: events carry CLOCK_MONOTONIC times */
static int slide_ms = 280, double_tap_ms = 300, invert;
static int long_is_y = 1, long_axis;   /* long_axis: 0 = default (Y), 1 = X, 2 = Y */
static int rng_min[2] = {0, 0}, rng_max[2] = {75, 283};   /* ABS_X, ABS_Y */

static int touch_device_name(int n, char *out, size_t cap)
{
    char p[128];
    FILE *f;

    snprintf(p, sizeof p, "/sys/class/input/event%d/device/name", n);
    f = fopen(p, "r");
    if (!f) return -1;
    if (!fgets(out, (int)cap, f)) out[0] = 0;
    fclose(f);
    out[strcspn(out, "\n")] = 0;
    return 0;
}

static int find_touch(char *out, size_t cap)
{
    const char *env = getenv("TOUCH_DEVICE");
    int n;

    if (env && *env) { snprintf(out, cap, "%s", env); return 0; }
    for (n = 0; n < 32; n++) {
        char name[128], lower[128];
        size_t i;

        if (touch_device_name(n, name, sizeof name) != 0) continue;
        for (i = 0; name[i] && i < sizeof lower - 1; i++)
            lower[i] = (name[i] >= 'A' && name[i] <= 'Z') ? (char)(name[i] + 32) : name[i];
        lower[i] = 0;
        if (strstr(lower, "touch")) { snprintf(out, cap, "/dev/input/event%d", n); return 0; }
    }
    snprintf(out, cap, "/dev/input/event0");
    return 0;
}

static void read_ranges(int fd)
{
    struct input_absinfo ai;

    /* The kernel drops a coordinate that has not changed since the last report, so the first
     * touch after this program starts may not repeat both. Start from what the device has now. */
    if (ioctl(fd, EVIOCGABS(ABS_X), &ai) == 0) { raw_x = ai.value; have_x = 1; if (ai.maximum > ai.minimum) { rng_min[0] = ai.minimum; rng_max[0] = ai.maximum; } }
    if (ioctl(fd, EVIOCGABS(ABS_Y), &ai) == 0) { raw_y = ai.value; have_y = 1; if (ai.maximum > ai.minimum) { rng_min[1] = ai.minimum; rng_max[1] = ai.maximum; } }
}

enum { A_NONE = 0, A_EXIT = 1, A_RESCHEDULE = 2 };
enum { G_IDLE, G_DOWN, G_DRAG };

static int gs = G_IDLE;                 /* what the finger is doing now */
static int contact, prev_contact;       /* finger down, as of this and the last report */
/* The touchscreen's coordinates are taken to be the display's own pixels (0..75 across, 0..283
 * along), which is what the stock screen program assumes too. What was actually seen is logged
 * so a different panel shows up in /tmp/be3600-player.log. */
static int seen, seen_lo[2], seen_hi[2];
static long long g_t0, pending_tap;     /* when this touch began; when a lone tap ended (0 = none) */
static int g_l0, g_s0, g_second, g_off, g_dir;
static long long last_draw_ns;
static int drag_dirty;
static struct { long long t; int off; } hist[8];
static int hist_n;

static void log_gesture(const char *what, int a, int b)
{
    fprintf(stderr, "be3600-player: %s %d %d\n", what, a, b);
    fflush(stderr);
}

static int sgn(int v) { return v > 0 ? 1 : v < 0 ? -1 : 0; }

/* Where the picture is while the finger is dragging: it follows the finger, but with
 * only one animation there is nothing to slide to, so it stretches a little and springs back. */
static int drag_shown_offset(int off, const struct neighbour *n)
{
    if (n) return off;
    if (off > 40) return 40;
    if (off < -40) return -40;
    return off / 2;
}

static void draw_drag(void)
{
    struct neighbour *n;
    int off = g_off;

    if (off == 0) { write_all(fb, shadow, FRAME_BYTES, 0); }
    else {
        n = neighbour_for(off > 0 ? 1 : -1);
        draw_slide(n ? n->frame : black, drag_shown_offset(off, n));
    }
    last_draw_ns = mono_ns();
    drag_dirty = 0;
}

static void finish_drag(void)
{
    int off = g_off, dir = off >= 0 ? 1 : -1, i;
    struct neighbour *n = neighbour_for(dir);
    double v = 0;
    int shown = drag_shown_offset(off, n);

    if (hist_n >= 2) {
        long long t1 = hist[hist_n - 1].t;
        int first = hist_n - 1;

        for (i = hist_n - 2; i >= 0 && t1 - hist[i].t <= 120; i--) first = i;
        if (t1 - hist[first].t >= 8) v = (double)(hist[hist_n - 1].off - hist[first].off) / (double)(t1 - hist[first].t);
    }

    if (n && (abs(off) >= COMMIT_PX || (abs(off) >= FLING_MIN_PX && (v * dir) >= FLING_SPEED))) {
        unsigned ms = (unsigned)((long long)slide_ms * (ROWS - abs(off)) / ROWS);

        if (ms < 90) ms = 90;
        log_gesture(dir > 0 ? "swipe: next, dragged" : "swipe: previous, dragged", abs(off), (int)(v * 1000));
        slide_between(n->frame, shown, dir * ROWS, ms);
        complete_switch(n);
    } else {
        log_gesture("swipe: snapped back, dragged", abs(off), (int)(v * 1000));
        slide_between(n ? n->frame : black, shown, 0, 140);
    }
}

/* A lone tap that nobody followed with a second one: go to the next animation. */
static void tap_switch(void)
{
    struct neighbour *n = neighbour_for(1);

    if (!n) {
        log_gesture("tap: only one saved animation", 0, 0);
        slide_between(black, 0, 26, 90);
        slide_between(black, 26, 0, 130);
        return;
    }
    log_gesture("tap: next", 0, 0);
    slide_between(n->frame, 0, ROWS, (unsigned)slide_ms);
    complete_switch(n);
}

static int on_down(long long now, int l, int s)
{
    gs = G_DOWN;
    g_t0 = now;
    g_l0 = l;
    g_s0 = s;
    g_off = 0;
    hist_n = 0;
    g_second = pending_tap != 0 && now - pending_tap <= double_tap_ms;
    lib_init();
    return A_NONE;
}

static int on_move(long long now, int l, int s)
{
    int dl = l - g_l0, ds = s - g_s0;

    if (gs == G_DOWN) {
        if (abs(ds) >= EXIT_PX && abs(ds) > abs(dl)) {
            log_gesture("swipe across the strip: leaving", ds, 0);
            return A_EXIT;
        }
        if (abs(dl) >= SLOP_PX && abs(dl) >= abs(ds)) {
            gs = G_DRAG;
            pending_tap = 0;
            g_second = 0;
        }
    }
    if (gs == G_DRAG) {
        /* the first SLOP_PX of movement is the tap's wobble allowance; the picture starts from the finger, not 12 px ahead of it */
        g_off = abs(dl) > SLOP_PX ? (dl > 0 ? dl - SLOP_PX : dl + SLOP_PX) : 0;
        if (invert) g_off = -g_off;
        g_dir = sgn(g_off);
        if (hist_n == 8) { memmove(hist, hist + 1, sizeof hist - sizeof hist[0]); hist_n--; }
        hist[hist_n].t = now;
        hist[hist_n].off = g_off;
        hist_n++;
        if (mono_ns() - last_draw_ns >= REDRAW_MS * 1000000LL) draw_drag();
        else drag_dirty = 1;
    }
    return A_NONE;
}

static int on_up(long long now)
{
    int res = A_NONE;

    fprintf(stderr, "be3600-player: touch ended at x %d y %d (began x %d y %d); raw x %d..%d y %d..%d seen so far\n", raw_x, raw_y, long_is_y ? g_s0 : g_l0, long_is_y ? g_l0 : g_s0, seen_lo[0], seen_hi[0], seen_lo[1], seen_hi[1]);
    fflush(stderr);

    if (gs == G_DRAG) {
        finish_drag();
        res = A_RESCHEDULE;
    } else if (gs == G_DOWN) {
        if (now - g_t0 <= TAP_MAX_MS) {
            if (g_second) {
                log_gesture("double-tap: leaving", 0, 0);
                pending_tap = 0;
                res = A_EXIT;
            } else {
                pending_tap = now;
            }
        } else {
            pending_tap = 0;
        }
    }
    gs = G_IDLE;
    return res;
}

/* One complete touch report: work out what changed. */
static int on_report(long long now)
{
    int l = long_is_y ? raw_y : raw_x;     /* along the strip */
    int s = long_is_y ? raw_x : raw_y;     /* across it */
    int res = A_NONE;

    if (!seen) { seen_lo[0] = seen_hi[0] = raw_x; seen_lo[1] = seen_hi[1] = raw_y; seen = 1; }
    if (raw_x < seen_lo[0]) seen_lo[0] = raw_x;
    if (raw_x > seen_hi[0]) seen_hi[0] = raw_x;
    if (raw_y < seen_lo[1]) seen_lo[1] = raw_y;
    if (raw_y > seen_hi[1]) seen_hi[1] = raw_y;

    if (contact && !prev_contact) res = on_down(now, l, s);
    else if (contact && prev_contact) res = on_move(now, l, s);
    else if (!contact && prev_contact) res = on_up(now);
    prev_contact = contact;
    return res;
}

/* Reads whatever the touchscreen has sent. Returns an A_* action, or -1 if the device broke. */
static int read_touch(void)
{
    struct input_event ev[32];
    int action = A_NONE;

    for (;;) {
        ssize_t got = read(tfd, ev, sizeof ev);
        size_t i, count;

        if (got < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN) break;
            return -1;
        }
        if (got == 0) return -1;
        count = (size_t)got / sizeof ev[0];

        for (i = 0; i < count; i++) {
            const struct input_event *e = &ev[i];
            int r;

            if (e->type == EV_ABS) {
                if (e->code == ABS_X || e->code == ABS_MT_POSITION_X) { raw_x = e->value; have_x = 1; }
                else if (e->code == ABS_Y || e->code == ABS_MT_POSITION_Y) { raw_y = e->value; have_y = 1; }
                else if (e->code == ABS_MT_TRACKING_ID) contact = e->value >= 0;
            } else if (e->type == EV_KEY && (e->code == BTN_TOUCH || e->code == BTN_TOOL_FINGER)) {
                contact = e->value != 0;
            } else if (e->type == EV_SYN && e->code == SYN_REPORT) {
                if (!have_x || !have_y) continue;
                r = on_report(ev_clock ? (long long)e->time.tv_sec * 1000 + e->time.tv_usec / 1000 : mono_ms());
                if (r == A_EXIT) return A_EXIT;
                if (r == A_RESCHEDULE) action = A_RESCHEDULE;
            }
        }
        if ((size_t)got < sizeof ev) break;
    }
    return action;
}

enum { S_TIME = 0, S_RESCHEDULE, S_EXIT, S_STOP, S_BROKEN };

/* Waits until deadline_ns while looking after the touchscreen. While a finger is
 * dragging, the animation is paused (the deadline is ignored) until it lets go. */
static int service(long long deadline_ns)
{
    for (;;) {
        long long wake = deadline_ns, now;
        int r;

        if (gs == G_DRAG) wake = mono_ns() + 200000000LL;
        if (pending_tap && gs != G_DOWN) {
            long long t = (pending_tap + double_tap_ms) * 1000000LL;

            if (t < wake) wake = t;
        }
        if (drag_dirty && last_draw_ns + REDRAW_MS * 1000000LL < wake) wake = last_draw_ns + REDRAW_MS * 1000000LL;

        r = wait_ready(wake, tfd);
        if (r == -1) return S_STOP;
        if (r == -2) return S_BROKEN;
        if (r == 1) {
            int a = read_touch();

            if (a < 0) return S_BROKEN;
            if (a == A_EXIT) return S_EXIT;
            if (a == A_RESCHEDULE) return S_RESCHEDULE;
        }

        now = mono_ns();
        if (drag_dirty && gs == G_DRAG && now - last_draw_ns >= REDRAW_MS * 1000000LL) draw_drag();
        if (pending_tap && gs != G_DOWN && now / 1000000LL - pending_tap >= double_tap_ms) {
            pending_tap = 0;
            tap_switch();
            return S_RESCHEDULE;
        }
        if (gs != G_DRAG && now >= deadline_ns) return S_TIME;
    }
}

static int show_touch_info(void)
{
    char dev[128], name[128] = "";
    int fd, n;

    find_touch(dev, sizeof dev);
    if (sscanf(dev, "/dev/input/event%d", &n) == 1) touch_device_name(n, name, sizeof name);
    fd = open(dev, O_RDONLY);
    if (fd < 0) return die(dev, strerror(errno));
    read_ranges(fd);
    printf("device %s (%s)\nABS_X %d..%d\nABS_Y %d..%d\nlong axis: %s\n", dev, name,
           rng_min[0], rng_max[0], rng_min[1], rng_max[1], long_is_y ? "Y" : "X");
    close(fd);
    return 0;
}

int main(int argc, char **argv)
{
    const char *path = "/etc/be3600-screen/active.bea";
    const char *touch_arg = NULL;
    char touch_dev[128];
    unsigned fade_ms = 0;
    int ai, gestures = 0, code = 0;
    /* The two test hooks work only when BE3600_TESTING is set, so nothing in a normal
     * environment can point the player at another file or stop it early. */
    const int testing = getenv("BE3600_TESTING") != NULL;
    const char *fbpath = testing ? getenv("BE3600_FB") : NULL;
    const char *loops_env = testing ? getenv("BE3600_LOOPS") : NULL;
    long max_loops = loops_env && *loops_env ? atol(loops_env) : 0;
    long loops = 0;
    long long tick_ns, next_ns;
    struct sigaction sa;

    for (ai = 1; ai < argc; ai++) {
        if (strcmp(argv[ai], "--version") == 0) {
            puts("be3600-player 3 (BEA1, BEA2, --fade, --gestures)");
            return 0;
        } else if (strcmp(argv[ai], "--fade") == 0 && ai + 1 < argc) {
            long v = atol(argv[ai + 1]);
            fade_ms = (v > 0 && v <= 5000) ? (unsigned)v : 0;
            ai++;
        } else if (strcmp(argv[ai], "--gestures") == 0) {
            gestures = 1;
        } else if (strcmp(argv[ai], "--touch") == 0 && ai + 1 < argc) {
            touch_arg = argv[++ai];
        } else if (strcmp(argv[ai], "--lib") == 0 && ai + 1 < argc) {
            lib_dir = argv[++ai];
        } else if (strcmp(argv[ai], "--slide") == 0 && ai + 1 < argc) {
            long v = atol(argv[++ai]);
            slide_ms = (v >= 0 && v <= 3000) ? (int)v : 280;
        } else if (strcmp(argv[ai], "--double-tap") == 0 && ai + 1 < argc) {
            long v = atol(argv[++ai]);
            double_tap_ms = (v >= 100 && v <= 2000) ? (int)v : 300;
        } else if (strcmp(argv[ai], "--swipe-invert") == 0) {
            invert = 1;
        } else if (strcmp(argv[ai], "--long-axis") == 0 && ai + 1 < argc) {
            const char *v = argv[++ai];

            long_axis = (v[0] == 'x' || v[0] == 'X') ? 1 : 2;   /* 1 = X, 2 = Y */
        } else if (strcmp(argv[ai], "--touch-info") == 0) {
            return show_touch_info();
        } else {
            path = argv[ai];
        }
    }
    active_path = path;

    if (anim_load(path, &cur) != 0) return 1;

    /* O_RDWR (not O_WRONLY): a fade needs to read back what is on the screen
     * now before blending into the first frame. */
    fb = open(fbpath ? fbpath : "/dev/fb0", O_RDWR | (fbpath ? O_CREAT : 0), 0644);
    if (fb < 0) return die(fbpath ? fbpath : "/dev/fb0", strerror(errno));

    if (gestures) {
        if (touch_arg) snprintf(touch_dev, sizeof touch_dev, "%s", touch_arg);
        else find_touch(touch_dev, sizeof touch_dev);
        tfd = open(touch_dev, O_RDONLY | O_NONBLOCK);
        if (tfd < 0) {
            die(touch_dev, strerror(errno));
            return 11;
        }
        read_ranges(tfd);
        {
            /* Ask for event times on the same clock as everything here, so a touch that waited in
             * the queue (while a slide was drawing) still keeps its real timing. */
            int clk = CLOCK_MONOTONIC;

            ev_clock = ioctl(tfd, EVIOCSCLOCKID, &clk) == 0;
        }
        if (long_axis) long_is_y = long_axis == 2;
        fprintf(stderr, "be3600-player: touch %s, long axis %s, ranges x %d..%d y %d..%d\n", touch_dev,
                long_is_y ? "Y" : "X", rng_min[0], rng_max[0], rng_min[1], rng_max[1]);
        fflush(stderr);
    }

    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal;             /* no SA_RESTART: a signal must interrupt the sleep */
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    signal(SIGCHLD, SIG_IGN);

    if (fade_ms > 0)
        fade_in(fb, cur.buf + cur.recs[0].off, fade_ms);

    next_ns = mono_ns();

    while (!stop_requested) {
        const struct record *r;
        long long now;

        if (rec_idx >= cur.n) {
            rec_idx = 0;
            loops++;
            if (max_loops > 0 && loops >= max_loops) break;
        }

        if (apply_record(&cur, rec_idx) != 0) { code = die("framebuffer", strerror(errno)); break; }
        r = &cur.recs[rec_idx];
        rec_idx++;

        tick_ns = 1000000000LL / (long long)cur.fps;

        /* Sleep until an absolute deadline so errors never accumulate. */
        next_ns += tick_ns * (long long)r->run;
        now = mono_ns();
        if (now - next_ns > 1000000000LL) next_ns = now;   /* far behind (e.g. the system was stalled): don't burst */

        if (tfd < 0) {
            wait_ready(next_ns, -1);
        } else {
            int s = service(next_ns);

            if (s == S_STOP) break;
            if (s == S_EXIT) { code = 10; break; }
            if (s == S_BROKEN) { die("touchscreen", "stopped working"); code = 11; break; }
            if (s == S_RESCHEDULE) next_ns = mono_ns();
        }
    }

    close(fb);
    if (tfd >= 0) close(tfd);
    anim_free(&cur);
    return code;
}
