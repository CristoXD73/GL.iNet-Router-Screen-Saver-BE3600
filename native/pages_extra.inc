/*
 * pages_extra.inc -- the pages that move: Aurora (your traffic as light), an analog clock face, the network doctor
 * and the live per-device bandwidth board. Then the table of every page. Included by be3600-player.c last.
 */

/* ---------------------------------------------------------------- page: aurora (traffic as light)
 *
 * Streaks of light flow across the strip: one way for what is coming down, the other for what is going up. When
 * nothing is happening they drift slowly like stars; during a download they become a river. It is a screensaver
 * that is also, quietly, a bandwidth meter.
 */
#define AUR_N 72

static struct streak { float x, y, v, len; uint8_t up, bright; } aur[AUR_N];
static int aur_ready;
static long long aur_last_ms;
static double aur_peak = 125000.0;          /* the busiest second lately: the scale everything is drawn against */

static unsigned aur_rnd_state = 2463534242u;

static float aur_rnd(void)                   /* 0..1, the same sequence every run, so a test render repeats exactly */
{
    aur_rnd_state ^= aur_rnd_state << 13;
    aur_rnd_state ^= aur_rnd_state >> 17;
    aur_rnd_state ^= aur_rnd_state << 5;
    return (float)(aur_rnd_state >> 8) / 16777216.0f;
}

static void aur_spawn(struct streak *s, int up, int anywhere)
{
    s->up = (uint8_t)up;
    s->y = 6.0f + aur_rnd() * (float)(CH - 12);
    s->v = 18.0f + aur_rnd() * 26.0f;
    s->len = 8.0f + aur_rnd() * 22.0f;
    s->bright = (uint8_t)(90 + (int)(aur_rnd() * 140.0f));
    s->x = anywhere ? aur_rnd() * (float)CW : (up ? (float)CW + s->len : -s->len);
}

static void aur_init(void)
{
    int i;

    if (aur_ready) return;
    aur_ready = 1;
    aur_rnd_state = 2463534242u;
    for (i = 0; i < AUR_N; i++) aur_spawn(&aur[i], i % 3 == 0, 1);
}

/* A streak: a short tail that fades out behind a bright head. */
static void aur_draw(const struct streak *s, uint16_t c, float scale)
{
    float dir = s->up ? -1.0f : 1.0f, len = s->len * scale;
    int k;

    for (k = 0; k < 4; k++) {
        float t0 = (float)k / 4.0f, t1 = (float)(k + 1) / 4.0f;
        int a = (int)((float)s->bright * (1.0f - t0) * (1.0f - t0));

        if (a < 6) continue;
        mk_line(s->x - dir * len * t0, s->y, s->x - dir * len * t1, s->y, 1.6f - 0.2f * (float)k);
        mk_flush_a(c, a);
    }
    mk_disc(s->x, s->y, 1.5f);
    mk_flush_a(C_WHITE, s->bright > 200 ? 210 : s->bright);
}

static void w_aurora(void)
{
    long long now = fake_now ? 0 : mono_ms();
    float dt = 0.04f;
    double busy = s_down + s_up;
    float rd, ru, scale;
    int i, want_d, want_u, nd = 0, nu = 0;
    char a[24], b[24];

    aur_init();
    if (!fake_now) {
        if (aur_last_ms) dt = (float)(now - aur_last_ms) / 1000.0f;
        aur_last_ms = now;
        if (dt < 0.005f) dt = 0.005f;
        if (dt > 0.2f) dt = 0.2f;
    }
    if (busy > aur_peak) aur_peak = busy;
    else aur_peak = aur_peak * 0.999 + busy * 0.001;          /* the scale drifts back down when things go quiet */
    if (aur_peak < 125000.0) aur_peak = 125000.0;
    rd = (float)(s_down / aur_peak);
    ru = (float)(s_up / aur_peak);
    if (rd > 1) rd = 1;
    if (ru > 1) ru = 1;

    /* the sky: a little brighter the busier the line is */
    cv_vgrad(0, CH, rgb(6, 10, 24 + (int)(26.0f * (rd + ru) / 2.0f)), C_BLACK);

    want_d = 6 + (int)(rd * 46.0f);
    want_u = 3 + (int)(ru * 20.0f);
    scale = 1.0f + 2.2f * (rd > ru ? rd : ru);
    for (i = 0; i < AUR_N; i++) {
        struct streak *s = &aur[i];
        int wanted = s->up ? (nu++ < want_u) : (nd++ < want_d);
        float speed = s->v * (1.0f + 5.0f * (s->up ? ru : rd));

        if (!wanted) continue;
        s->x += (s->up ? -speed : speed) * dt;
        if (!s->up && s->x - s->len * scale > (float)CW) aur_spawn(s, 0, 0);
        if (s->up && s->x + s->len * scale < 0) aur_spawn(s, 1, 0);
        aur_draw(s, s->up ? C_BLUE : C_CYAN, scale);
    }

    {                                                         /* a soft shadow under the readout so it stays legible */
        int k;

        for (k = 0; k < 22; k++) cv_rect_a(0, CH - 1 - k, CW, 1, C_BLACK, 150 - k * 7);
    }
    if (!s_have_net) { ut(T_S, 12, CH - 16, "no internet interface", C_TERT); return; }
    fmt_rate(s_down, a, sizeof a);
    fmt_rate(s_up, b, sizeof b);
    cv_dot(15.0f, (float)CH - 12.5f, 2.6f, C_CYAN);
    ut(T_S, 22, CH - 17, a, C_WHITE);
    cv_dot(15.0f + 84.0f, (float)CH - 12.5f, 2.6f, C_BLUE);
    ut(T_S, 22 + 84, CH - 17, b, C_GRAY);
}

/* ---------------------------------------------------------------- page: analog clock */
static void clock_hand(float cx, float cy, float deg, float len, float back, float w, uint16_t c)
{
    float s = fsin_d(deg), co = fcos_d(deg);

    mk_line(cx - s * back, cy + co * back, cx + s * len, cy - co * len, w);
    mk_flush(c);
}

static void w_analog(void)
{
    static const char *days[] = { "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday" };
    float cx = 40.0f, cy = 38.0f, r = 33.0f;
    float sec = (float)W_tm.tm_sec, mn = (float)W_tm.tm_min, hr = (float)(W_tm.tm_hour % 12);
    char t[16];
    int i, h12 = W_tm.tm_hour % 12;

    if (!fake_now) sec += (float)(wall_ms() % 1000) / 1000.0f;      /* a smooth sweep, not a tick */
    mn += sec / 60.0f;
    hr += mn / 60.0f;
    if (!h12) h12 = 12;

    mk_arc(cx, cy, r + 2.0f, 1.2f, 0, 359.5f);
    mk_flush_a(C_GRAY, 90);
    for (i = 0; i < 60; i++) {                                       /* the minute and hour marks */
        float d = (float)i * 6.0f, s = fsin_d(d), co = fcos_d(d);
        int big = i % 5 == 0;

        mk_line(cx + s * (r - (big ? 6.0f : 3.0f)), cy - co * (r - (big ? 6.0f : 3.0f)), cx + s * (r - 1.0f), cy - co * (r - 1.0f), big ? 2.0f : 1.0f);
        mk_flush(big ? C_WHITE : C_DIM);
    }
    clock_hand(cx, cy, hr * 30.0f, r * 0.55f, 6.0f, 5.4f, C_WHITE);
    clock_hand(cx, cy, mn * 6.0f, r * 0.82f, 7.0f, 3.8f, C_WHITE);
    clock_hand(cx, cy, sec * 6.0f, r * 0.86f, 10.0f, 1.5f, C_ORANGE);
    cv_dot(cx, cy, 3.4f, C_ORANGE);
    cv_dot(cx, cy, 1.5f, C_BLACK);

    ut_label(92, 12, days[W_tm.tm_wday], C_ORANGE);
    ut_f(T_L, 92, 25, C_WHITE, "%d %s", W_tm.tm_mday, mon[W_tm.tm_mon]);
    if (cfg_24h) snprintf(t, sizeof t, "%02d:%02d", W_tm.tm_hour, W_tm.tm_min);
    else snprintf(t, sizeof t, "%d:%02d %s", h12, W_tm.tm_min, W_tm.tm_hour >= 12 ? "PM" : "AM");
    ut(T_M, 92, 54, t, C_GRAY);
}

/* ---------------------------------------------------------------- page: network doctor
 *
 * Where the trouble is: this router, the link to the ISP, the internet itself, and name lookups. Each stop is
 * timed by the helper; the first broken link is named in plain words.
 */
struct hop { const char *name; double ms; int ok; };

static void doctor_node(float x, float y, int ok, int active, uint16_t c)
{
    if (active) { mk_disc(x, y, 9.0f); mk_flush_a(c, 60); }
    cv_dot(x, y, 6.0f, ok ? c : C_RED);
    cv_dot(x, y, 3.4f, C_BLACK);
    if (!ok) { cv_line(x - 2.6f, y - 2.6f, x + 2.6f, y + 2.6f, 1.6f, C_RED); cv_line(x + 2.6f, y - 2.6f, x - 2.6f, y + 2.6f, 1.6f, C_RED); }
}

static void w_doctor(void)
{
    char b[400], v[64];
    struct hop hops[4] = { { "Router", 0, 1 }, { "ISP link", -1, 0 }, { "Internet", -1, 0 }, { "Names", -1, 0 } };
    const char *headline = "All clear";
    uint16_t hc = C_GREEN;
    int i, bad = -1;
    float x0 = 26.0f, step = 68.0f, y = 30.0f;

    if (data_file("path.txt", b, sizeof b) <= 0) { no_data("NETWORK DOCTOR", "waiting for be3600-widgetd"); return; }
    if (file_age_s("path.txt") > 90) { no_data("NETWORK DOCTOR", "helper is not updating"); return; }
    if (kv_get(b, "gw_ms", v, sizeof v)) { hops[1].ms = atof(v); hops[1].ok = hops[1].ms >= 0; }
    if (kv_get(b, "net_ms", v, sizeof v)) { hops[2].ms = atof(v); hops[2].ok = hops[2].ms >= 0; }
    if (kv_get(b, "dns_ms", v, sizeof v)) { hops[3].ms = atof(v); hops[3].ok = hops[3].ms >= 0; }

    for (i = 1; i < 4; i++) if (!hops[i].ok) { bad = i; break; }
    if (bad == 1) { headline = "Router can't reach the ISP"; hc = C_RED; }
    else if (bad == 2) { headline = "Your ISP is down"; hc = C_RED; }
    else if (bad == 3) { headline = "Name lookups are failing"; hc = C_ORANGE; }
    else if (hops[2].ms > 200) { headline = "Internet is slow"; hc = C_ORANGE; }
    else if (hops[3].ms > 300) { headline = "Name lookups are slow"; hc = C_ORANGE; }

    for (i = 0; i < 4; i++) {
        float x = x0 + (float)i * step;

        if (i > 0) {
            int broken = bad == i;
            float a = x0 + (float)(i - 1) * step + 9.0f, bx = x - 9.0f;

            if (broken) {                                            /* a torn line where it stops working */
                cv_line(a, y, a + (bx - a) * 0.38f, y, 1.6f, C_DIM);
                cv_line(bx - (bx - a) * 0.38f, y, bx, y, 1.6f, C_DIM);
                cv_line(a + (bx - a) * 0.45f, y - 5.0f, a + (bx - a) * 0.55f, y + 5.0f, 1.6f, C_RED);
            } else cv_line(a, y, bx, y, 1.6f, hops[i].ok ? C_GREEN : C_DIM);
        }
        doctor_node(x, y, hops[i].ok, bad < 0 && i == 3, i == 0 ? C_GREEN : hops[i].ok ? C_GREEN : C_RED);
        ut_c(T_XS, (int)x, 42, hops[i].name, C_GRAY);
        if (hops[i].ms >= 0) { snprintf(v, sizeof v, "%.0f ms", hops[i].ms); ut_c(T_S, (int)x, 52, v, C_WHITE); }
        else if (i > 0) ut_c(T_S, (int)x, 52, "--", C_TERT);
        else ut_c(T_S, (int)x, 52, "here", C_TERT);
    }
    cv_dot(15.0f, 12.5f, 3.0f, hc);
    ut(T_MB, 24, 7, headline, hc);
}

/* ---------------------------------------------------------------- page: who is using the internet
 *
 * Live bytes per device, read straight out of the kernel's connection table (nothing is added to the firewall).
 */
struct talker { char name[40]; double down, up; };

static int load_talkers(struct talker *t, int max, double *tot_d, double *tot_u)
{
    char b[4096], *line, *save = NULL;
    int n = 0;

    *tot_d = *tot_u = 0;
    if (data_file("talkers.txt", b, sizeof b) <= 0 || file_age_s("talkers.txt") > 30) return -1;
    for (line = strtok_r(b, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char name[64] = "";
        double d = 0, u = 0;

        if (sscanf(line, "dev|%63[^|]|%lf|%lf", name, &d, &u) == 3) {
            if (n < max) { snprintf(t[n].name, sizeof t[n].name, "%s", name); t[n].down = d; t[n].up = u; n++; }
            *tot_d += d;
            *tot_u += u;
        }
    }
    return n;
}

static void w_talkers(void)
{
    struct talker t[8];
    double tot_d, tot_u, top = 0;
    int n = load_talkers(t, 8, &tot_d, &tot_u), i, rows;
    char a[24];

    if (n < 0) { no_data("IN USE NOW", "waiting for be3600-widgetd"); return; }
    ut(T_MB, 14, 7, "In use now", C_WHITE);
    fmt_rate(tot_d + tot_u, a, sizeof a);
    ut_r(T_S, CW - 14, 8, a, tot_d + tot_u > 0 ? C_GREEN : C_GRAY);
    if (n > 3) ut_f(T_XS, 104, 9, C_TERT, "+%d MORE", n - 3);
    if (n == 0) { ut(T_M, 14, 38, "Nothing is talking to the internet", C_GRAY); return; }
    for (i = 0; i < n; i++) if (t[i].down + t[i].up > top) top = t[i].down + t[i].up;
    if (top <= 0) top = 1;
    rows = n > 3 ? 3 : n;
    for (i = 0; i < rows; i++) {
        int y = 26 + i * 17;
        double share = (t[i].down + t[i].up) * 100.0 / top;

        ut_fit(T_S, 14, y, 104, t[i].name, C_WHITE);
        cv_capsule(122, (float)y + 1, 96, 8, share, t[i].down >= t[i].up ? C_CYAN : C_BLUE);
        fmt_rate(t[i].down + t[i].up, a, sizeof a);
        ut_r(T_S, CW - 14, y, a, C_GRAY);
    }
}
