/*
 * pages_basic.inc -- the four plainest pages: clock, network speed, vitals rings, router info.
 * Included by be3600-player.c after sample.inc.
 */

/* ---------------------------------------------------------------- page: clock */
static void w_clock(void)
{
    static const char *days[] = { "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday" };
    char hm[16], ap[4] = "";
    int h = W_tm.tm_hour, sx = ((W_tm.tm_min / 2) % 5) - 2, sy = ((W_tm.tm_min / 3) % 3) - 1;   /* drifts a pixel or two so nothing burns in */
    int x;

    if (!cfg_24h) { snprintf(ap, sizeof ap, "%s", h >= 12 ? "PM" : "AM"); h %= 12; if (!h) h = 12; }
    if (cfg_24h) snprintf(hm, sizeof hm, "%02d:%02d", h, W_tm.tm_min);
    else snprintf(hm, sizeof hm, "%d:%02d", h, W_tm.tm_min);
    ut(T_XXL, 14 + sx, 20 + sy, hm, C_WHITE);
    x = 14 + ut_w(T_XXL, hm) + 20 + sx;
    ut_label(x, 14 + sy, days[W_tm.tm_wday], C_RED);
    ut_f(T_L, x, 27 + sy, C_WHITE, "%d %s", W_tm.tm_mday, mon[W_tm.tm_mon]);
    if (*ap) ut_label(x, 52 + sy, ap, C_GRAY);
    cv_capsule((float)x, (float)(60 + sy), (float)(CW - 14 - x), 4, (W_tm.tm_sec + 1) * 100.0 / 60.0, C_CYAN);
}

/* ---------------------------------------------------------------- page: network speed */
static void rate_parts(double bps, char *num, size_t ncap, char *unit, size_t ucap)
{
    double v = speed_bits ? bps * 8.0 : bps;
    const char *u = speed_bits ? "b/s" : "B/s";
    char scale = 0;

    if (v >= 1e9) { v /= 1e9; scale = 'G'; }
    else if (v >= 1e6) { v /= 1e6; scale = 'M'; }
    else if (v >= 1e3) { v /= 1e3; scale = 'K'; }
    if (v < 100 && scale) snprintf(num, ncap, "%.1f", v); else snprintf(num, ncap, "%.0f", v);
    snprintf(unit, ucap, "%s%s", scale ? (char[2]){ scale, 0 } : "", u);
}

static void speed_row(int y, const char *label, uint16_t c, double bps)
{
    char n[24], u[16];

    rate_parts(bps, n, sizeof n, u, sizeof u);
    cv_dot(17.0f, (float)(y + 3), 3.0f, c);
    ut_label(26, y, label, C_GRAY);
    ut(T_L, 14, y + 11, n, C_WHITE);
    ut(T_S, 14 + ut_w(T_L, n) + 4, y + 18, u, C_GRAY);
}

static void w_netspeed(void)
{
    char mx[32];
    double top = 0;
    int i;

    if (!s_have_net) { no_data("NETWORK", "no internet interface"); return; }
    speed_row(8, "Download", C_GREEN, s_down);
    speed_row(42, "Upload", C_BLUE, s_up);
    cv_rect(114, 10, 1, CH - 20, C_DIM);
    for (i = 0; i < HIST; i++) { if (h_down[i] > top) top = h_down[i]; if (h_up[i] > top) top = h_up[i]; }
    if (top < 12500) top = 12500;      /* never zoom in on line noise: the scale is at least 100 kb/s */
    cv_rect(126, CH - 12, CW - 138, 1, C_DIM);
    cv_graph(126, 10, CW - 138, CH - 22, h_down, HIST, top, C_GREEN, 1);
    cv_graph(126, 10, CW - 138, CH - 22, h_up, HIST, top, C_BLUE, 0);
    fmt_rate(top, mx, sizeof mx);
    ut_r(T_XS, CW - 12, 6, mx, C_TERT);
    ut(T_XS, 126, CH - 9, "60 s", C_TERT);
    ut_r(T_XS, CW - 12, CH - 9, wan_if, C_TERT);
}

/* ---------------------------------------------------------------- page: system vitals (four activity rings) */
static void vitals_ring(int i, const char *label, double pct, const char *val, uint16_t c)
{
    int cx = 41 + i * 67, cy = 30;
    int w = ut_w(T_MB, val);

    cv_ring((float)cx, (float)cy, 22.0f, 6.0f, pct, c);
    ut(T_MB, cx - w / 2 - (label[0] == 'T' ? 2 : 0), cy - 5, val, C_WHITE);
    ut_c(T_XS, cx, 62, label, C_GRAY);
}

static void w_vitals(void)
{
    char v[24];

    snprintf(v, sizeof v, "%.0f", s_cpu);
    vitals_ring(0, "CPU", s_cpu, v, level_color(s_cpu, 70, 90));
    snprintf(v, sizeof v, "%.0f", s_mem);
    vitals_ring(1, "MEMORY", s_mem, v, level_color(s_mem, 75, 90));
    snprintf(v, sizeof v, "%.0f", s_flash);
    vitals_ring(2, "STORAGE", s_flash, v, level_color(s_flash, 80, 92));
    if (s_temp >= 0) {
        snprintf(v, sizeof v, "%.0f", s_temp);
        vitals_ring(3, "TEMP", (s_temp - 30.0) * 100.0 / 60.0, v, level_color(s_temp, 70, 85));
        cv_dot((float)(41 + 3 * 67 + ut_w(T_MB, v) / 2 - 1), 25.0f, 1.6f, C_WHITE);       /* the degree sign */
    } else vitals_ring(3, "TEMP", 0, "--", C_DIM);
}

/* ---------------------------------------------------------------- page: router info */
static void info_row(int y, const char *label, const char *value, uint16_t c)
{
    ut_label(150, y + 2, label, C_TERT);
    ut_r(T_M, CW - 14, y, value, c);
}

static void w_info(void)
{
    char up[32], lan[32] = "-", wan[40] = "-", fw[64] = "", model[64] = "", b[24];

    human_dur((long)s_uptime, up, sizeof up);
    ip_of("br-lan", lan, sizeof lan);
    if (*wan_if) ip_of(wan_if, wan, sizeof wan);
    if (slurp_rp("/etc/glversion", fw, sizeof fw) > 0) fw[strcspn(fw, "\r\n")] = 0;
    if (slurp_rp("/tmp/sysinfo/model", model, sizeof model) > 0) model[strcspn(model, "\r\n")] = 0;
    ut_label(14, 9, "Uptime", C_GRAY);
    ut_fit(T_L, 14, 22, 122, up, C_WHITE);
    ut_fit(T_S, 14, 52, 122, *model ? model : "GL.iNet router", C_TERT);
    cv_rect(140, 10, 1, CH - 20, C_DIM);
    snprintf(b, sizeof b, "%.2f", s_load1);
    info_row(9, "LAN", lan, C_WHITE);
    info_row(26, "WAN", wan, C_WHITE);
    info_row(43, "Load", b, C_WHITE);
    info_row(60, "Firmware", *fw ? fw : "-", C_CYAN);
}

