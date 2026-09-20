/*
 * pages_touch.inc -- alerts, the pages you can touch (timers, message board, guest Wi-Fi switch), the Wi-Fi QR code,
 * weather and custom-script pages. Included by be3600-player.c after qr.inc.
 */

/* ---------------------------------------------------------------- alerts (banners that interrupt the current page) */
enum { AL_INFO, AL_GOOD, AL_WARN, AL_BAD };
struct alert { char text[80]; int level; };
static struct alert aq[4];
static int aq_n;
static long ev_offset = -1;
static int cfg_alerts = 1;
static int cfg_chime;                 /* FAN_CHIME: a banner is also heard, on the cooling fan */

static void alert_push(int level, const char *text)
{
    if (!cfg_alerts || aq_n >= 4) return;
    aq[aq_n].level = level;
    snprintf(aq[aq_n].text, sizeof aq[aq_n].text, "%s", text);
    aq_n++;
}

static int alert_pop(struct alert *out)
{
    if (aq_n == 0) return 0;
    *out = aq[0];
    memmove(aq, aq + 1, (size_t)(aq_n - 1) * sizeof aq[0]);
    aq_n--;
    return 1;
}

/* New lines in the helper's event file: epoch|level|text (events older than a minute are ignored). */
static void events_poll(void)
{
    char p[600];
    FILE *f;
    char line[200];
    long size;

    snprintf(p, sizeof p, "%s/events", ddir);
    f = fopen(p, "r");
    if (!f) { ev_offset = -2; return; }                              /* no file yet: whatever gets written later is new */
    fseek(f, 0, SEEK_END);
    size = ftell(f);
    if (ev_offset == -2 || ev_offset > size) ev_offset = 0;          /* created since the last look, or cleared: read it all */
    else if (ev_offset < 0) ev_offset = size;                        /* first look at a file that was already there: skip what is old */
    fseek(f, ev_offset, SEEK_SET);
    while (fgets(line, sizeof line, f)) {
        long long ts = 0;
        char lv[16] = "", text[100] = "";

        if (sscanf(line, "%lld|%15[^|]|%99[^\n]", &ts, lv, text) == 3 && (W_t - ts) < 60 && (W_t - ts) > -60)
            alert_push(strcmp(lv, "bad") == 0 ? AL_BAD : strcmp(lv, "warn") == 0 ? AL_WARN : strcmp(lv, "good") == 0 ? AL_GOOD : AL_INFO, text);
    }
    ev_offset = ftell(f);
    fclose(f);
}

static void draw_banner(const struct alert *a, long long t_ms)
{
    uint16_t fg = a->level == AL_BAD ? C_RED : a->level == AL_WARN ? C_ORANGE : a->level == AL_GOOD ? C_GREEN : C_CYAN;
    float e = t_ms >= 320 ? 1.0f : (float)t_ms / 320.0f;
    int dy, tw = ut_w(T_L, a->text), x, cw = CW - 82;

    e = 1.0f - (1.0f - e) * (1.0f - e) * (1.0f - e);        /* the card slides down and settles */
    dy = (int)(-(float)CH * (1.0f - e));
    cv_fill(C_BLACK);
    cv_card(6.0f, 6.0f + (float)dy, (float)CW - 12.0f, (float)CH - 12.0f, 16.0f, C_CARD);
    cv_dot(34.0f, 38.0f + (float)dy, 15.0f, fg);
    if (a->level == AL_GOOD) { cv_line(27, 38 + (float)dy, 32, 43 + (float)dy, 3.0f, C_WHITE); cv_line(32, 43 + (float)dy, 42, 32 + (float)dy, 3.0f, C_WHITE); }
    else if (a->level == AL_INFO) { cv_dot(34.0f, 30.0f + (float)dy, 2.0f, C_WHITE); cv_line(34, 36 + (float)dy, 34, 46 + (float)dy, 3.0f, C_WHITE); }
    else { cv_line(34, 29 + (float)dy, 34, 39 + (float)dy, 3.2f, C_WHITE); cv_dot(34.0f, 46.0f + (float)dy, 2.0f, C_WHITE); }
    if (tw <= cw) x = 60 + (cw - tw) / 2;
    else x = 60 + 10 - (int)((t_ms / 20) % (tw + 70));         /* scrolls left, about 50 pixels a second */
    clip_x0 = 58; clip_x1 = CW - 14;
    ut(T_L, x, 30 + dy, a->text, C_WHITE);
    clip_x0 = 0; clip_x1 = CW;
}


/* ---------------------------------------------------------------- actions run by touch (guest Wi-Fi) */
static const char *action_cmd = "/usr/sbin/be3600-widget-action";

static void run_action(const char *what, const char *arg)
{
    pid_t p = fork();

    if (p == 0) {
        int dn = open("/dev/null", O_RDWR);

        if (dn >= 0) { dup2(dn, 0); dup2(dn, 1); dup2(dn, 2); }
        execl(action_cmd, action_cmd, what, arg, (char *)NULL);
        _exit(127);
    }
}

/* ---------------------------------------------------------------- Pomodoro timer */
static int cfg_pomo[3] = { 25, 5, 15 };
static struct { int mode, state, cycles; long long end, left; } pt;      /* state: 0 idle, 1 running, 2 paused */
static int pt_loaded;

static void pt_path(char *out, size_t cap) { snprintf(out, cap, "%s/pomodoro.state", ddir); }

static void pt_save(void)
{
    char p[600];
    FILE *f;

    pt_path(p, sizeof p);
    f = fopen(p, "w");
    if (!f) return;
    fprintf(f, "mode=%d\nstate=%d\ncycles=%d\nend=%lld\nleft=%lld\n", pt.mode, pt.state, pt.cycles, pt.end, pt.left);
    fclose(f);
}

static void pt_load(void)
{
    char p[600], b[300], v[40];

    if (pt_loaded) return;
    pt_loaded = 1;
    pt_path(p, sizeof p);
    if (slurp(p, b, sizeof b) <= 0) return;
    if (kv_get(b, "mode", v, sizeof v)) pt.mode = atoi(v) % 3;
    if (kv_get(b, "state", v, sizeof v)) pt.state = atoi(v) % 3;
    if (kv_get(b, "cycles", v, sizeof v)) pt.cycles = atoi(v);
    if (kv_get(b, "end", v, sizeof v)) pt.end = atoll(v);
    if (kv_get(b, "left", v, sizeof v)) pt.left = atoll(v);
}

static long long pt_left(void)
{
    long long l;

    if (pt.state == 1) { l = pt.end - (long long)W_t; return l < 0 ? 0 : l; }
    if (pt.state == 2) return pt.left;
    return (long long)cfg_pomo[pt.mode] * 60;
}

static int pt_tap(void)
{
    pt_load();
    if (pt.state == 0) { pt.state = 1; pt.end = (long long)W_t + (long long)cfg_pomo[pt.mode] * 60; }
    else if (pt.state == 1) { pt.left = pt.end - (long long)W_t; pt.state = 2; }
    else { pt.state = 1; pt.end = (long long)W_t + pt.left; }
    pt_save();
    return 1;
}

static int pt_hold(void)
{
    pt_load();
    pt.mode = 0; pt.state = 0; pt.cycles = 0;
    pt_save();
    return 1;
}

/* Called every second whatever page is showing: the timer rings even when you are looking at something else. */
static void pt_check(void)
{
    pt_load();
    if (pt.state == 1 && (long long)W_t >= pt.end) {
        int was = pt.mode;

        pt.state = 0;
        if (was == 0) { pt.cycles++; pt.mode = (pt.cycles % 4 == 0) ? 2 : 1; alert_push(AL_GOOD, "Focus done: take a break"); }
        else { pt.mode = 0; alert_push(AL_INFO, "Break over: back to it"); }
        pt_save();
    }
}

static void w_pomodoro(void)
{
    static const char *names[3] = { "Focus", "Break", "Long break" };
    uint16_t col = pt.mode == 0 ? C_RED : pt.mode == 1 ? C_GREEN : C_CYAN;
    long long l, total;
    char t[40];
    int i, tw;
    const char *btn;

    pt_load();
    l = pt_left();
    total = (long long)cfg_pomo[pt.mode] * 60;
    cv_ring(40.0f, 38.0f, 28.0f, 7.0f, pt.state == 0 ? 100.0 : (double)(l * 100) / (double)(total > 0 ? total : 1), col);
    snprintf(t, sizeof t, "%02lld:%02lld", l / 60, l % 60);
    tw = ut_w(T_MB, t);
    ut(T_MB, 40 - tw / 2, 33, t, C_WHITE);
    ut_label(98, 10, "Pomodoro", C_GRAY);
    ut(T_L, 98, 22, names[pt.mode], C_WHITE);
    btn = pt.state == 1 ? "Pause" : pt.state == 2 ? "Resume" : "Start";
    cv_pill(98, 50, 88, 19, pt.state == 1 ? C_ORANGE : C_GREEN);
    ut_c(T_MB, 98 + 44, 55, btn, C_WHITE);
    for (i = 0; i < 4; i++) cv_dot((float)(CW - 20 - (3 - i) * 13), 60.0f, 3.2f, i < (pt.cycles % 4) ? col : C_DIM);
    ut_r(T_XS, CW - 14, 10, "HOLD TO RESET", C_TERT);
}


/* ---------------------------------------------------------------- stopwatch */
static struct { int state; long long start_ms, acc_ms; } sw;      /* running from start_ms (wall clock, ms), or holding acc_ms */
static int sw_loaded;

static long long wall_ms(void)
{
    struct timespec t;

    clock_gettime(CLOCK_REALTIME, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static void sw_path(char *out, size_t cap) { snprintf(out, cap, "%s/stopwatch.state", ddir); }

static void sw_save(void)
{
    char p[600];
    FILE *f;

    sw_path(p, sizeof p);
    f = fopen(p, "w");
    if (!f) return;
    fprintf(f, "state=%d\nstart=%lld\nacc=%lld\n", sw.state, sw.start_ms, sw.acc_ms);
    fclose(f);
}

static void sw_load(void)
{
    char p[600], b[200], v[40];

    if (sw_loaded) return;
    sw_loaded = 1;
    sw_path(p, sizeof p);
    if (slurp(p, b, sizeof b) <= 0) return;
    if (kv_get(b, "state", v, sizeof v)) sw.state = atoi(v) ? 1 : 0;
    if (kv_get(b, "start", v, sizeof v)) sw.start_ms = atoll(v);
    if (kv_get(b, "acc", v, sizeof v)) sw.acc_ms = atoll(v);
}

static long long sw_elapsed(void) { return sw.acc_ms + (sw.state ? wall_ms() - sw.start_ms : 0); }

static int sw_tap(void)
{
    sw_load();
    if (sw.state) { sw.acc_ms = sw_elapsed(); sw.state = 0; }
    else { sw.start_ms = wall_ms(); sw.state = 1; }
    sw_save();
    return 1;
}

static int sw_hold(void)
{
    sw_load();
    sw.state = 0; sw.acc_ms = 0; sw.start_ms = 0;
    sw_save();
    return 1;
}

static void w_stopwatch(void)
{
    long long e;
    char t[24], c[8];
    int cs, s, m, h, tw;

    sw_load();
    e = sw_elapsed();
    cs = (int)((e / 10) % 100); s = (int)((e / 1000) % 60); m = (int)((e / 60000) % 60); h = (int)(e / 3600000);
    if (h > 0) snprintf(t, sizeof t, "%d:%02d:%02d", h, m, s);
    else snprintf(t, sizeof t, "%02d:%02d", m, s);
    snprintf(c, sizeof c, ".%02d", cs);
    ut_label(14, 8, "Stopwatch", sw.state ? C_GREEN : C_GRAY);
    if (h > 0) { tw = ut(T_XL, 14, 26, t, C_WHITE); ut(T_L, 14 + tw + 4, 33, c, C_GRAY); }
    else { tw = ut(T_XXL, 14, 22, t, C_WHITE); ut(T_L, 14 + tw + 4, 40, c, C_GRAY); }
    cv_pill(CW - 14 - 62, 27, 62, 22, sw.state ? C_RED : C_GREEN);
    ut_c(T_MB, CW - 14 - 31, 32, sw.state ? "Stop" : e ? "Resume" : "Start", C_WHITE);
    ut(T_XS, 14, 64, "HOLD TO RESET", C_TERT);
}


/* ---------------------------------------------------------------- message board */
static void w_message(void)
{
    char b[300], *msg = b;
    uint16_t col = C_WHITE;
    int tw, x, i;

    if (slurp_rp("/etc/be3600-screen/message", b, sizeof b) <= 0 || !b[strspn(b, " \r\n\t")]) {
        ut_label(14, 12, "Message", C_GRAY);
        ut(T_M, 14, 32, "Nothing to say yet.", C_GRAY);
        ut(T_S, 14, 54, "On the router:  be3600-anim say \"hello\"", C_CYAN);
        return;
    }
    b[strcspn(b, "\r\n")] = 0;
    if (b[0] == '!') {                       /* !red text, !green text ... picks the colour */
        char name[16] = "";
        int n = 0;

        if (sscanf(b + 1, "%15s%n", name, &n) >= 1) {
            if (!strcmp(name, "red")) col = C_RED; else if (!strcmp(name, "green")) col = C_GREEN;
            else if (!strcmp(name, "yellow")) col = C_YELLOW; else if (!strcmp(name, "cyan")) col = C_CYAN;
            else if (!strcmp(name, "orange")) col = C_ORANGE; else n = 0;
            if (n) msg = b + 1 + n + strspn(b + 1 + n, " ");
        }
    }
    tw = ut_w(T_L, msg);
    if (tw <= CW - 28) x = (CW - tw) / 2;
    else {                                    /* starts at the left, scrolls out, then comes back in from the right */
        int pos = (int)((W_ms / 20) % (tw + CW));

        x = pos <= tw + 16 ? 16 - pos : CW - (pos - tw - 16);
    }
    ut(T_L, x, (CH - 15) / 2, msg, col);
    for (i = 0; i < 26; i++) {               /* the text fades out at both edges */
        int a = 255 - i * 10;

        cv_rect_a(i, 0, 1, CH, C_BLACK, a);
        cv_rect_a(CW - 1 - i, 0, 1, CH, C_BLACK, a);
    }
}


/* ---------------------------------------------------------------- guest Wi-Fi switch */
static int hold_prog;             /* 0..100 while a finger is being held on a page that has a hold action */

static void w_guest(void)
{
    char b[300], st[16] = "", ssid[64] = "";
    int on;
    float kx;

    if (data_file("guest.txt", b, sizeof b) <= 0) { no_data("GUEST WI-FI", "waiting for be3600-widgetd"); return; }
    kv_get(b, "state", st, sizeof st);
    kv_get(b, "ssid", ssid, sizeof ssid);
    if (!*st || !strcmp(st, "none")) { no_data("GUEST WI-FI", "no guest network found"); return; }
    on = strcmp(st, "on") == 0;
    ut_label(14, 9, "Guest Wi-Fi", C_GRAY);
    ut(T_L, 14, 22, on ? "On" : "Off", on ? C_GREEN : C_WHITE);
    ut_fit(T_M, 14, 48, 176, ssid, C_GRAY);
    cv_pill(CW - 14 - 66, 19, 66, 38, on ? C_GREEN : rgb(58, 58, 60));
    kx = on ? (float)(CW - 14 - 66 + 66 - 19) : (float)(CW - 14 - 66 + 19);
    cv_dot(kx, 38.0f + 1.2f, 15.5f, rgb(20, 20, 20));      /* a soft shadow, then the knob */
    cv_dot(kx, 38.0f, 15.0f, C_WHITE);
    if (hold_prog > 0) {
        cv_capsule(14, CH - 8, CW - 28, 4, hold_prog, C_CYAN);
        ut_r(T_XS, CW - 14, 62 - 8, on ? "KEEP HOLDING TO TURN OFF" : "KEEP HOLDING TO TURN ON", C_CYAN);
    } else ut_r(T_XS, CW - 14, 64, on ? "HOLD TO TURN OFF" : "HOLD TO TURN ON", C_TERT);
}


static int guest_hold(void)
{
    run_action("guest", "toggle");
    return 1;
}

/* ---------------------------------------------------------------- Wi-Fi QR code */
static void qr_escape(const char *in, char *out, size_t cap)
{
    size_t o = 0;

    for (; *in && o + 3 < cap; in++) {
        if (strchr("\\;,:\"", *in)) out[o++] = '\\';
        out[o++] = *in;
    }
    out[o] = 0;
}

static void w_wifiqr(void)
{
    char b[400], ssid[64] = "", key[96] = "", enc[16] = "", es[160], ek[200], payload[400];
    int n, q, scale = 2, x0, y0, i, j, side;

    if (data_file("wifiqr.txt", b, sizeof b) <= 0) { no_data("WI-FI QR", "waiting for be3600-widgetd"); return; }
    kv_get(b, "ssid", ssid, sizeof ssid);
    kv_get(b, "key", key, sizeof key);
    kv_get(b, "enc", enc, sizeof enc);
    if (!*ssid) { no_data("WI-FI QR", "no network chosen (WIFI_QR_SECTION)"); return; }
    qr_escape(ssid, es, sizeof es);
    qr_escape(key, ek, sizeof ek);
    if (!*key || !strcmp(enc, "nopass")) snprintf(payload, sizeof payload, "WIFI:T:nopass;S:%s;;", es);
    else snprintf(payload, sizeof payload, "WIFI:T:WPA;S:%s;P:%s;;", es, ek);
    n = qr_encode(payload, (int)strlen(payload));
    if (n == 0 || n > 37) { no_data("WI-FI QR", "name or password too long for a QR"); return; }
    q = (CH - 4) / scale / 2 - n / 2;
    if (q > 4) q = 4;
    if (q < 0) q = 0;
    side = (n + 2 * q) * scale;
    x0 = 5; y0 = (CH - side) / 2;
    cv_card((float)x0 - 1, (float)y0 - 1, (float)side + 2, (float)side + 2, 7.0f, C_WHITE);
    for (j = 0; j < n; j++)
        for (i = 0; i < n; i++)
            if (qr_m[j][i]) cv_rect(x0 + (i + q) * scale, y0 + (j + q) * scale, scale, scale, C_BLACK);
    {
        int tx = x0 + side + 18;

        ut_label(tx, 12, "Join Wi-Fi", C_GRAY);
        ut_fit(T_L, tx, 26, CW - tx - 12, ssid, C_WHITE);
        ut(T_S, tx, 56, "Point a phone camera at the code", C_CYAN);
    }
}


/* ---------------------------------------------------------------- weather */
static void wx_cloud(float cx, float cy, uint16_t c)
{
    cv_dot(cx - 12, cy + 5, 8.0f, c);
    cv_dot(cx, cy - 2, 12.0f, c);
    cv_dot(cx + 13, cy + 4, 9.0f, c);
    cv_card(cx - 12, cy + 2, 25.0f, 11.0f, 5.5f, c);
}

static const char *wx_text(int code)
{
    if (code == 0) return "Clear";
    if (code <= 2) return "Partly cloudy";
    if (code == 3) return "Overcast";
    if (code == 45 || code == 48) return "Fog";
    if (code >= 51 && code <= 57) return "Drizzle";
    if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return "Rain";
    if ((code >= 71 && code <= 77) || code == 85 || code == 86) return "Snow";
    if (code >= 95) return "Thunderstorm";
    return "Weather";
}

static void wx_icon(float cx, float cy, int code)
{
    uint16_t cloud = rgb(196, 202, 212);
    int i;

    if (code <= 2) {
        static const signed char ray[8][4] = { { 19, 0, 24, 0 }, { 13, 13, 17, 17 }, { 0, 19, 0, 24 }, { -13, 13, -17, 17 },
                                               { -19, 0, -24, 0 }, { -13, -13, -17, -17 }, { 0, -19, 0, -24 }, { 13, -13, 17, -17 } };
        float sx = code == 0 ? cx : cx - 8, sy = code == 0 ? cy : cy - 9;

        cv_dot(sx, sy, code == 0 ? 13.0f : 11.0f, C_YELLOW);
        if (code == 0) for (i = 0; i < 8; i++) cv_line(cx + ray[i][0], cy + ray[i][1], cx + ray[i][2], cy + ray[i][3], 2.2f, C_YELLOW);
    }
    if (code >= 1) wx_cloud(cx + (code <= 2 ? 7 : 0), cy + (code <= 2 ? 8 : 0), cloud);
    if (code == 45 || code == 48) for (i = 0; i < 3; i++) cv_pill(cx - 20, cy + 16 + (float)i * 5, 40, 2.6f, cloud);
    if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) for (i = 0; i < 4; i++) cv_line(cx - 15 + (float)i * 10, cy + 18, cx - 19 + (float)i * 10, cy + 27, 2.2f, C_CYAN);
    if ((code >= 71 && code <= 77) || code == 85 || code == 86) for (i = 0; i < 4; i++) cv_dot(cx - 15 + (float)i * 10, cy + 22 + (float)(i % 2) * 4, 2.2f, C_WHITE);
    if (code >= 95) { cv_line(cx + 2, cy + 14, cx - 5, cy + 24, 2.6f, C_YELLOW); cv_line(cx - 5, cy + 24, cx + 3, cy + 24, 2.6f, C_YELLOW); cv_line(cx + 3, cy + 24, cx - 4, cy + 34, 2.6f, C_YELLOW); }
}

static void w_weather(void)
{
    char b[300], v[40], place[48] = "", unit[4] = "C";
    double temp, hi = 0, lo = 0;
    int code = 0, have_hl = 0, w;
    char t[16];

    if (data_file("weather.txt", b, sizeof b) <= 0) { no_data("WEATHER", "waiting for be3600-widgetd"); return; }
    if (!kv_get(b, "temp", v, sizeof v)) { no_data("WEATHER", "no forecast yet"); return; }
    temp = atof(v);
    if (kv_get(b, "code", v, sizeof v)) code = atoi(v);
    if (kv_get(b, "hi", v, sizeof v)) { hi = atof(v); have_hl = 1; }
    if (kv_get(b, "lo", v, sizeof v)) lo = atof(v);
    kv_get(b, "place", place, sizeof place);
    kv_get(b, "unit", unit, sizeof unit);
    wx_icon(40.0f, 34.0f, code);
    snprintf(t, sizeof t, "%.0f", temp);
    w = ut(T_XL, 92, 10, t, C_WHITE);
    mk_arc(92.0f + (float)w + 6.0f, 16.0f, 3.0f, 1.8f, 0, 359.5f);
    mk_flush(C_WHITE);
    ut(T_L, 92 + w + 13, 10, unit, C_GRAY);
    ut_fit(T_MB, 92, 42, CW - 106, wx_text(code), C_CYAN);
    if (have_hl) ut_f(T_S, 92, 60, C_GRAY, "H %.0f    L %.0f", hi, lo);
    if (*place) ut_r(T_XS, CW - 14, 10, place, C_TERT);
    if (file_age_s("weather.txt") > 3 * 3600) ut_r(T_XS, CW - 14, 62, "OUT OF DATE", C_ORANGE);
}


/* ---------------------------------------------------------------- custom pages (your own scripts) */
static char cur_param[40];

static uint16_t color_named(const char *n, uint16_t dflt)
{
    if (!strcmp(n, "green")) return C_GREEN;
    if (!strcmp(n, "red")) return C_RED;
    if (!strcmp(n, "yellow")) return C_YELLOW;
    if (!strcmp(n, "cyan")) return C_CYAN;
    if (!strcmp(n, "blue")) return C_BLUE;
    if (!strcmp(n, "orange")) return C_ORANGE;
    if (!strcmp(n, "white")) return C_WHITE;
    if (!strcmp(n, "gray")) return C_GRAY;
    return dflt;
}

static void w_custom(void)
{
    char fn[80], b[2048], *line, *save = NULL;
    char title[48] = "", big[24] = "", lines[3][64], sp[400] = "";
    int nl = 0, have_bar = 0, i, ns = 0, gx = 14, gw = CW - 28, bigw;
    double bar = 0, series[HIST];
    uint16_t col = C_WHITE;

    snprintf(fn, sizeof fn, "custom-%s.txt", cur_param);
    if (data_file(fn, b, sizeof b) <= 0) { no_data(cur_param, "waiting for be3600-widgetd"); return; }
    for (line = strtok_r(b, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char *val = strchr(line, ':');

        if (!val) continue;
        *val++ = 0;
        while (*val == ' ') val++;
        if (!strcmp(line, "title")) snprintf(title, sizeof title, "%s", val);
        else if (!strcmp(line, "big")) snprintf(big, sizeof big, "%s", val);
        else if (!strcmp(line, "line") && nl < 3) { snprintf(lines[nl], sizeof lines[nl], "%s", val); nl++; }
        else if (!strcmp(line, "bar")) { bar = atof(val); have_bar = 1; }
        else if (!strcmp(line, "spark")) snprintf(sp, sizeof sp, "%s", val);
        else if (!strcmp(line, "color")) col = color_named(val, C_WHITE);
    }
    if (*sp) {
        const char *q = sp;

        while (*q && ns < HIST) { char *e; double d = strtod(q, &e); if (e == q) break; series[ns++] = d; q = e; while (*q == ' ') q++; }
    }
    ut_label(14, 8, *title ? title : cur_param, C_GRAY);
    if (ns >= 2) { gx = 168; gw = CW - gx - 14; cv_graph(gx, 16, gw, 40, series, ns, 0, col, 1); }
    bigw = ns >= 2 ? 148 : CW - 28;
    if (*big) {
        int f = ut_is_num(big) ? T_XL : T_L;

        ut_fit(f, 14, f == T_XL ? 22 : 26, bigw, big, col);
    }
    for (i = 0; i < nl; i++) {
        int y = 52 + i * 12;

        if (y + 9 > CH - (have_bar ? 12 : 2)) break;
        ut_fit(T_S, 14, y, bigw, lines[i], C_GRAY);
    }
    if (have_bar) cv_capsule(14, CH - 10, CW - 28, 6, bar, level_color(bar, 75, 90));
}

