/*
 * hello.inc -- the welcome the router gives itself once it is installed: the two eyes from Motion
 * Studio's logo wake up, look around and settle, and then it says it is ready.
 *
 * It is timed to the "rev" chime on the cooling fan (be3600-fan chime rev), so the eyes widen on
 * each stab of throttle and open out on the long pull at the end. Both are started together by
 * be3600-anim hello. On a router with no fan you just get the eyes.
 */

#define HELLO_MS 7950                   /* the length of the rev chime */

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

/* One eye, with its glow. open is 0..1 of full height, and it keeps its centre as it closes. */
static void h_eye(float cx, float cy, float open, float glow)
{
    float h = EYE_H * open, w = EYE_W, r = w / 2.0f;

    if (h < 1.5f) h = 1.5f;
    if (h < r * 2.0f) r = h / 2.0f;
    if (glow > 0.02f) {                 /* three soft passes stand in for a blur */
        mk_rrect(cx - w / 2 - 5, cy - h / 2 - 5, w + 10, h + 10, r + 5);
        mk_flush_a(C_BLUE, (int)(38 * glow));
        mk_rrect(cx - w / 2 - 3, cy - h / 2 - 3, w + 6, h + 6, r + 3);
        mk_flush_a(C_BLUE, (int)(55 * glow));
        mk_rrect(cx - w / 2 - 1.5f, cy - h / 2 - 1.5f, w + 3, h + 3, r + 1.5f);
        mk_flush_a(C_CYAN, (int)(70 * glow));
    }
    mk_rrect(cx - w / 2, cy - h / 2, w, h, r);
    mk_flush(C_WHITE);
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
     *   5350 - 7950  the long pull away               open out, slide left, and say it is ready
     */

    /* looking about, the way the logo does */
    static const float kx[] = { 0,0,  600,0,  900,-14,  1300,-14,  1600,12,  2000,12,  2300,0,  2750,0,
                                3100,-16,  3550,-16,  3900,0,  4150,0,  4450,17,  5050,17,  5350,0,  5700,0,
                                6300,-40,  7950,-40 };
    static const float ky[] = { 0,0,  900,-4,  1300,-4,  1600,4,  2000,4,  2300,0,  3100,-6,  3550,-6,
                                4450,5,  5050,5,  5350,0,  7950,0 };
    /* the lids: shut, then open as it winds up, blinking in every cut and wide on every stab */
    static const float ko[] = { 0,0,  250,0,  700,1,  850,1.12f,  1200,1,  2250,1,  2380,0.06f,  2560,1,
                                2750,1,  2900,1.18f,  3400,1,  3700,1,  3820,0.06f,  4000,1,  4150,1,
                                4300,1.18f,  4900,1,  5150,1,  5250,0.10f,  5380,1,  5600,1.22f,  7950,1.08f };
    /* the glow follows the throttle */
    static const float kg[] = { 0,0,  250,0,  700,0.35f,  1400,0.60f,  2200,0.85f,  2500,0.35f,  2900,0.90f,
                                3500,0.75f,  3900,0.30f,  4400,0.92f,  5000,0.80f,  5250,0.45f,  5700,1.0f,  7950,0.95f };
    static const float kt[] = { 0,0,  6250,0,  6900,1,  7950,1 };          /* the word fading in, once the eyes have settled */

    float dx = h_track(kx, 18, (float)t), dy = h_track(ky, 12, (float)t);
    float open = h_track(ko, 22, (float)t), glow = h_track(kg, 14, (float)t);
    float tx = h_track(kt, 4, (float)t);
    float cx = CW / 2.0f + dx, cy = CH / 2.0f + dy;

    cv_fill(C_BLACK);
    cv_vgrad(0, CH, rgb(6, 10, 20), C_BLACK);
    h_eye(cx - (EYE_W + EYE_GAP) / 2.0f, cy, open, glow);
    h_eye(cx + (EYE_W + EYE_GAP) / 2.0f, cy, open, glow);

    if (tx > 0.02f) {
        int x = 152;
        /* Fading the ink towards the background is right here (the background is black) and, unlike
         * veiling the area afterwards, it cannot dim an eye that is still sliding past. */
        uint16_t white = rgb((unsigned)(255 * tx), (unsigned)(255 * tx), (unsigned)(255 * tx));
        uint16_t grey = rgb((unsigned)(142 * tx), (unsigned)(142 * tx), (unsigned)(147 * tx));

        clip_x0 = 140; clip_x1 = CW;
        ut(T_XS, x, 24, "READY", grey);
        ut_fit(T_M, x, 48, CW - x - 8, "Swipe to explore", white);
        clip_x0 = 0; clip_x1 = CW;
    }
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
