/*
 * hello.inc -- the welcome the router gives itself once it is installed: the two eyes from Motion
 * Studio's logo wake up, look around and settle, then shrink to two dots beside the KVM OCCULT logo,
 * as a particle crosses it.
 *
 * It is timed to the "rev" chime on the cooling fan (be3600-fan chime rev), so the eyes widen on
 * each stab of throttle and open out on the long pull at the end. Both are started together by
 * be3600-anim hello. On a router with no fan you just get the eyes.
 */

#define HELLO_MS 9600                   /* the rev chime (7950 ms), then the logo holds */

#include "hello_logo.inc"

/* The logo's eyes are 8 x 13 with a 6 gap; this is the same shape, as big as the strip allows. */
#define EYE_W  26.0f
#define EYE_H  42.0f
#define EYE_GAP 18.0f

static float h_ease(float t)            /* slow in, slow out */
{
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    return t * t * (3.0f - 2.0f * t);
}

/* Where a value sits on a line of keyframes: pairs of (time in ms, value). */
static float h_track(const float *kf, int n, float t)
{
    int i;

    if (t <= kf[0]) return kf[1];
    for (i = 0; i < n - 1; i++) {
        float t0 = kf[i * 2], t1 = kf[i * 2 + 2];

        if (t < t1) return kf[i * 2 + 1] + (kf[i * 2 + 3] - kf[i * 2 + 1]) * h_ease((t - t0) / (t1 - t0));
    }
    return kf[(n - 1) * 2 + 1];
}

/* One eye, with its glow. open is 0..1 of full height, and it keeps its centre as it closes;
 * sc shrinks the whole eye (1 = full size). */
static void h_eye(float cx, float cy, float open, float glow, float sc)
{
    float h = EYE_H * open * sc, w = EYE_W * sc, r = w / 2.0f, k = 0.5f + 0.5f * sc;

    if (h < 1.5f) h = 1.5f;
    if (h < r * 2.0f) r = h / 2.0f;
    if (glow > 0.02f) {                 /* three soft passes stand in for a blur */
        mk_rrect(cx - w / 2 - 5 * k, cy - h / 2 - 5 * k, w + 10 * k, h + 10 * k, r + 5 * k);
        mk_flush_a(C_BLUE, (int)(38 * glow));
        mk_rrect(cx - w / 2 - 3 * k, cy - h / 2 - 3 * k, w + 6 * k, h + 6 * k, r + 3 * k);
        mk_flush_a(C_BLUE, (int)(55 * glow));
        mk_rrect(cx - w / 2 - 1.5f * k, cy - h / 2 - 1.5f * k, w + 3 * k, h + 3 * k, r + 1.5f * k);
        mk_flush_a(C_CYAN, (int)(70 * glow));
    }
    mk_rrect(cx - w / 2, cy - h / 2, w, h, r);
    mk_flush(C_WHITE);
}

/* A value along straight lines between keyframes: pairs of (time in ms, value). */
static float h_lin(const float *kf, int n, float t)
{
    int i;

    if (t <= kf[0]) return kf[1];
    for (i = 0; i < n - 1; i++)
        if (t < kf[i * 2 + 2]) return kf[i * 2 + 1] + (kf[i * 2 + 3] - kf[i * 2 + 1]) * (t - kf[i * 2]) / (kf[i * 2 + 2] - kf[i * 2]);
    return kf[(n - 1) * 2 + 1];
}

/* The logo's coverage at (x, y) on the canvas; 0 outside it. */
static int h_logo_a(int x, int y)
{
    if (x < LOGO_X || x >= LOGO_X + LOGO_W || y < LOGO_Y || y >= LOGO_Y + LOGO_H) return 0;
    return hello_logo[y - LOGO_Y][x - LOGO_X];
}

/* A number from a position, the same every time: the sparks' "randomness", so every frame can be
 * drawn again exactly (the tests compare them). */
static unsigned h_hash(unsigned a, unsigned b)
{
    unsigned h = a * 2654435761u ^ (b + 0x9e3779b9u + (a << 6) + (a >> 2));

    h ^= h >> 15; h *= 2246822519u; h ^= h >> 13; h *= 3266489917u; h ^= h >> 16;
    return h;
}

/* Light added on top of what is there (a = 0..255 of colour r, g, b): the particle and its sparks. */
static void h_add(int x, int y, int r, int g, int b, int a)
{
    uint16_t o;
    int rr, gg, bb;

    if (a <= 0 || x < 0 || x >= CW || y < 0 || y >= CH) return;
    o = cv[y][x];
    rr = ((o >> 11) & 31) + r * a / (255 * 8);
    gg = ((o >> 5) & 63) + g * a / (255 * 4);
    bb = (o & 31) + b * a / (255 * 8);
    if (rr > 31) rr = 31;
    if (gg > 63) gg = 63;
    if (bb > 31) bb = 31;
    cv[y][x] = (uint16_t)((rr << 11) | (gg << 5) | bb);
}

/* A soft round glow: brightest in the middle, gone at radius rad. */
static void h_glow(float cx, float cy, float rad, int r, int g, int b, int a)
{
    int x, y;

    for (y = (int)(cy - rad) - 1; y <= (int)(cy + rad) + 1; y++)
        for (x = (int)(cx - rad) - 1; x <= (int)(cx + rad) + 1; x++) {
            float dx = ((float)x + 0.5f - cx) / rad, dy = ((float)y + 0.5f - cy) / rad, d = dx * dx + dy * dy;

            if (d < 1.0f) h_add(x, y, r, g, b, (int)((float)a * (1.0f - d) * (1.0f - d)));
        }
}

/* The particle's path over the logo: in from the left and off the right edge, with a little rise and fall.
 * It leaves the screen at 7950 ms, as the rev lets go (the fan starts 150 ms ahead, so they are heard together). */
#define P_T0 6780
#define P_T1 8230
static int h_particle(int t, float *px, float *py)
{
    float u;

    if (t < P_T0 || t > P_T1) return 0;
    u = (float)(t - P_T0) / (float)(P_T1 - P_T0);
    u = u * u * (3.0f - 2.0f * u) * 0.7f + u * 0.3f;          /* gathers speed, then lets go */
    *px = 30.0f + 290.0f * u;                                  /* off the right edge, so the wake settles */
    *py = 40.0f + 9.0f * fsin_d(u * 360.0f * 1.15f);
    return 1;
}

/* How much the particle stirs the pixel (x, y) up: 0 (not at all) .. 1, close to it and in its wake. */
static float h_stir(int x, int y, float px, float py)
{
    float dx = (float)x - px, dy = ((float)y - py) / 34.0f, head, wake = 0;

    head = dx * dx / (26.0f * 26.0f) + dy * dy;
    head = head < 1.0f ? (1.0f - head) * (1.0f - head) : 0.0f;
    if (dx < 0 && dx > -90.0f) {                                /* behind it, settling down again */
        float along = 1.0f + dx / 90.0f, across = 1.0f - dy * dy * 1.6f;

        if (across > 0) wake = along * along * across * 0.75f;
    }
    return head > wake ? head : wake;
}

/* The logo in grey level lvl (0..1). While the particle crosses, each pixel near it is pushed
 * sideways by a ripple, blurred a little and split into red and blue, as much as it is stirred,
 * and brightened as the particle charges it. Every pixel is worked out on its own. */
static void h_logo(float lvl, int t)
{
    int x, y, k, have;
    float px = 0, py = 0;

    if (lvl <= 0.0f) return;
    have = h_particle(t, &px, &py);
    for (y = LOGO_Y; y < LOGO_Y + LOGO_H; y++)
        for (x = LOGO_X; x < LOGO_X + LOGO_W; x++) {
            float st = have ? h_stir(x, y, px, py) : 0.0f;

            if (st < 0.02f) {
                int a = hello_logo[y - LOGO_Y][x - LOGO_X], ink = (int)(255 * lvl);

                if (a) cv_blend(x, y, rgb((unsigned)ink, (unsigned)ink, (unsigned)ink), a);
            } else {
                /* a ripple along the rows that travels with the particle, and a split that grows with it */
                float wave = fsin_d((float)y * 23.0f + ((float)x - px) * 7.0f - (float)t * 0.9f) * 6.0f * st;
                int split = (int)(3.5f * st + 0.5f), rad = (int)(3.0f * st + 0.5f), c, i;
                int off[3], bg[3], top[3] = { 31, 63, 31 }, out[3];
                int shift = (int)(wave >= 0 ? wave + 0.5f : wave - 0.5f);
                float gain = lvl * (1.0f + 0.35f * st);
                uint16_t o = cv[y][x];

                off[0] = split; off[1] = 0; off[2] = -split;
                bg[0] = (o >> 11) & 31; bg[1] = (o >> 5) & 63; bg[2] = o & 31;
                for (c = 0; c < 3; c++) {
                    int sum = 0;
                    float v;

                    for (i = -rad; i <= rad; i++) sum += h_logo_a(x + off[c] + shift + i, y);
                    v = (float)sum / (float)(2 * rad + 1) / 255.0f * gain;
                    if (v > 1.0f) v = 1.0f;
                    out[c] = bg[c] + (int)((float)(top[c] - bg[c]) * v);
                }
                cv[y][x] = (uint16_t)((out[0] << 11) | (out[1] << 5) | out[2]);
            }
        }
    if (!have) return;

    /* the sparks it sheds: each is born where the particle was, drifts off and fades */
    for (k = 0; k < 40; k++) {
        int born = P_T0 + k * (P_T1 - P_T0) / 40, age = t - born;
        unsigned h = h_hash((unsigned)k, 77u);
        float sx, sy, life = 260.0f + (float)(h % 240), f, vx, vy;

        if (age < 0 || (float)age > life || !h_particle(born, &sx, &sy)) continue;
        f = (float)age / life;
        vx = -0.02f - (float)((h >> 8) % 100) / 2500.0f;
        vy = ((float)((h >> 16) % 200) - 100.0f) / 1800.0f;
        sx += vx * (float)age + ((float)((h >> 4) % 9) - 4.0f);
        sy += vy * (float)age;
        h_glow(sx, sy, 1.8f, (h & 1) ? 120 : 255, (h & 1) ? 230 : 120, 255, (int)(230 * (1.0f - f)));
    }
    /* and the particle itself: a white-hot core in a cyan haze */
    h_glow(px, py, 16.0f, 40, 170, 255, 110);
    h_glow(px, py, 6.0f, 150, 230, 255, 220);
    h_glow(px, py, 2.5f, 255, 255, 255, 255);
}

static void hello_frame(int t)
{
    /* The rev, and what the eyes do over it:
     *      0 - 2200  the engine starts and winds up   eyes wake, open, glance left then right
     *   2200 - 2750  cut                              blink
     *   2750 - 3650  stab                             widen, snap left
     *   3650 - 4150  cut                              blink
     *   4150 - 5150  stab                             widen, snap right
     *   5150 - 5350  short cut                        a quick blink
     *   5350 - 7950  the long pull away               open out, then shrink to two dots up on the left
     *                                                 while KVM OCCULT fades in and a particle crosses it
     */

    /* looking about, the way the logo does */
    static const float kx[] = { 0,0,  600,0,  900,-14,  1300,-14,  1600,12,  2000,12,  2300,0,  2750,0,
                                3100,-16,  3550,-16,  3900,0,  4150,0,  4450,17,  5050,17,  5350,0,  5910,0,
                                7020,-59,  9600,-59 };
    static const float ky[] = { 0,0,  900,-4,  1300,-4,  1600,4,  2000,4,  2300,0,  3100,-6,  3550,-6,
                                4450,5,  5050,5,  5350,0,  5910,0,  7020,-16,  9600,-16 };
    /* the lids: shut, then open as it winds up, blinking in every cut and wide on every stab; round at the end */
    static const float ko[] = { 0,0,  250,0,  700,1,  850,1.12f,  1200,1,  2250,1,  2380,0.06f,  2560,1,
                                2750,1,  2900,1.18f,  3400,1,  3700,1,  3820,0.06f,  4000,1,  4150,1,
                                4300,1.18f,  4900,1,  5150,1,  5250,0.10f,  5380,1,  5600,1.22f,  5910,1.2135f,
                                7020,0.62f,  9600,0.62f };
    /* the glow follows the throttle */
    static const float kg[] = { 0,0,  250,0,  700,0.35f,  1400,0.60f,  2200,0.85f,  2500,0.35f,  2900,0.90f,
                                3500,0.75f,  3900,0.30f,  4400,0.92f,  5000,0.80f,  5250,0.45f,  5700,1.0f,  7950,0.95f };
    /* the eyes shrink to dots (the gap between them with them) */
    static const float ks[] = { 0,1,  5910,1,  7020,0.3636f,  9600,0.3636f };
    /* the logo comes up out of the dark (a particle crosses it on the way: h_logo) */
    static const float kl[] = { 6000,0,  6200,0.11f,  6400,0.27f,  6600,0.44f,  6800,0.65f,  7000,0.82f,
                                7200,0.93f,  7400,1,  9600,1 };

    float dx = h_track(kx, 18, (float)t), dy = h_track(ky, 14, (float)t);
    float open = h_track(ko, 24, (float)t), glow = h_track(kg, 14, (float)t);
    float sc = h_track(ks, 4, (float)t), lvl = h_lin(kl, 9, (float)t);
    float cx = CW / 2.0f + dx, cy = CH / 2.0f + dy, half = (EYE_W + EYE_GAP) / 2.0f * sc;

    cv_fill(C_BLACK);
    cv_vgrad(0, CH, rgb(6, 10, 20), C_BLACK);
    h_logo(lvl, t);
    h_eye(cx - half, cy, open, glow, sc);
    h_eye(cx + half, cy, open, glow, sc);
}

/* Play it once, straight to the screen, and stop. */
static void run_hello(int ms)
{
    long long start = mono_ms(), next = mono_ns();
    int t;

    if (ms <= 0 || ms > 30000) ms = HELLO_MS;
    while (!stop_requested && (t = (int)(mono_ms() - start)) <= ms) {
        hello_frame(fake_now ? ms / 2 : t);
        cv_to_frame(shadow);
        write_all(fb, shadow, FRAME_BYTES, 0);
        if (fake_now) break;                        /* the tests want one deterministic frame */
        next += 40000000LL;                         /* 25 a second is plenty for this */
        wait_ready(next, -1);
    }
}

/* Tests only (BE3600_TESTING): every frame of the welcome, 40 ms apart, as raw screen files
 * DIR/f00000.bin, DIR/f00040.bin, ... so it can be looked at and compared off the router. */
static int hello_dump(const char *dir, int ms)
{
    char path[512];
    int t;

    if (ms <= 0 || ms > 30000) ms = HELLO_MS;
    for (t = 0; t <= ms; t += 40) {
        int fd;

        hello_frame(t);
        cv_to_frame(shadow);
        snprintf(path, sizeof path, "%s/f%05d.bin", dir, t);
        fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd < 0) return 1;
        write_all(fd, shadow, FRAME_BYTES, 0);
        close(fd);
    }
    return 0;
}
