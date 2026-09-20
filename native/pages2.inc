/*
 * pages2.inc -- widget pages that show what the helper be3600-widgetd collects (Wi-Fi clients, internet health,
 * VPN) or that keep books themselves (data usage, overall health). Included by be3600-player.c after widgets.inc.
 */

/* ---------------------------------------------------------------- page: Wi-Fi clients */
#define MAXROWS 32
struct client { char name[64], ip[32]; int dbm; };

static int load_clients(struct client *c, int max, int *have_signal)
{
    char b[6144], *line, *save = NULL;
    int n = 0;

    *have_signal = 0;
    if (data_file("wifi.txt", b, sizeof b) > 0 && file_age_s("wifi.txt") < 90) {
        *have_signal = 1;
        for (line = strtok_r(b, "\n", &save); line && n < max; line = strtok_r(NULL, "\n", &save)) {
            char name[64] = "", ip[32] = "";
            int dbm = 0;

            if (sscanf(line, "client|%63[^|]|%31[^|]|%d", name, ip, &dbm) >= 2) {
                snprintf(c[n].name, sizeof c[n].name, "%s", name);
                snprintf(c[n].ip, sizeof c[n].ip, "%s", ip);
                c[n].dbm = dbm;
                n++;
            }
        }
        return n;
    }
    /* No helper data: the DHCP leases still say who has an address. */
    if (slurp_rp("/tmp/dhcp.leases", b, sizeof b) > 0) {
        for (line = strtok_r(b, "\n", &save); line && n < max; line = strtok_r(NULL, "\n", &save)) {
            char mac[32], ip[32], name[64];

            if (sscanf(line, "%*s %31s %31s %63s", mac, ip, name) == 3) {
                snprintf(c[n].name, sizeof c[n].name, "%s", strcmp(name, "*") == 0 ? mac : name);
                snprintf(c[n].ip, sizeof c[n].ip, "%s", ip);
                c[n].dbm = 0;
                n++;
            }
        }
    }
    return n;
}

static void signal_bars(int x, int y, int dbm)
{
    int lvl = dbm >= -55 ? 4 : dbm >= -65 ? 3 : dbm >= -75 ? 2 : 1, i;
    uint16_t on = lvl >= 3 ? C_GREEN : lvl == 2 ? C_ORANGE : C_RED;

    for (i = 0; i < 4; i++) {
        float h = 3.0f + (float)i * 2.4f;

        cv_card((float)x + (float)i * 4.0f, (float)y + 10.0f - h, 2.6f, h, 1.2f, i < lvl ? on : C_DIM);
    }
}

static uint16_t avatar_color(const char *name)
{
    static const uint16_t pal[] = { 0, 0, 0, 0, 0, 0 };
    unsigned h = 0;
    const char *p;

    (void)pal;
    for (p = name; *p; p++) h = h * 31 + (unsigned char)*p;
    switch (h % 6) {
    case 0: return C_BLUE;
    case 1: return C_PURPLE;
    case 2: return C_ORANGE;
    case 3: return C_GREEN;
    case 4: return C_CYAN;
    default: return rgb(255, 55, 95);
    }
}

static void w_clients(void)
{
    struct client c[MAXROWS];
    int sig, n = load_clients(c, MAXROWS, &sig), i, first = 0, rows = 3;
    char hd[24];

    ut(T_MB, 14, 8, "Wi-Fi", C_WHITE);
    snprintf(hd, sizeof hd, "%d %s", n, n == 1 ? "device" : "devices");
    ut_r(T_S, CW - 14, 9, hd, n ? C_GREEN : C_GRAY);
    if (n == 0) { ut(T_M, 14, 38, "Nobody is connected", C_GRAY); return; }
    if (n > rows) first = (int)((W_t / 3) % (n - rows + 1));          /* long lists scroll a row every 3 s */
    for (i = 0; i < rows && first + i < n; i++) {
        int y = 24 + i * 17;
        char ini[2] = { (char)toupper((unsigned char)c[first + i].name[0]), 0 };

        cv_dot(20.0f, (float)y + 6.5f, 6.5f, avatar_color(c[first + i].name));
        ut_c(T_XS, 20, y + 3, ini, C_WHITE);
        ut_fit(T_M, 34, y + 2, 118, c[first + i].name, C_WHITE);
        ut_fit(T_S, 160, y + 3, 92, c[first + i].ip, C_TERT);
        if (sig) signal_bars(CW - 26, y + 2, c[first + i].dbm);
    }
    if (n > rows) {
        int k;

        for (k = 0; k <= n - rows; k++) cv_dot((float)CW - 6.0f, 26.0f + (float)k * (44.0f / (float)(n - rows + 1)), 1.4f, k == first ? C_WHITE : C_DIM);
    }
}


/* ---------------------------------------------------------------- page: internet health */
struct ping_stats { int n, valid, offline, last; double avg, loss; double v[HIST]; char target[48]; int stale; };

static int load_ping(struct ping_stats *p)
{
    char b[1024], ms[900];
    const char *q;
    int i;
    double sum = 0;

    memset(p, 0, sizeof *p);
    p->last = -1;
    if (data_file("ping.txt", b, sizeof b) <= 0) return -1;
    kv_get(b, "target", p->target, sizeof p->target);
    if (!kv_get(b, "ms", ms, sizeof ms)) return -1;
    p->stale = file_age_s("ping.txt") > 40;
    for (q = ms; *q && p->n < HIST;) {
        char *e;
        double v = strtod(q, &e);

        if (e == q) break;
        p->v[p->n++] = v;
        q = e;
        while (*q == ' ') q++;
    }
    for (i = 0; i < p->n; i++) {
        if (p->v[i] >= 0) { p->valid++; sum += p->v[i]; p->last = (int)(p->v[i] + 0.5); }
    }
    if (p->valid) p->avg = sum / p->valid;
    if (p->n) p->loss = 100.0 * (p->n - p->valid) / p->n;
    if (p->n >= 3 && p->v[p->n - 1] < 0 && p->v[p->n - 2] < 0 && p->v[p->n - 3] < 0) p->offline = 1;
    return 0;
}

static void w_internet(void)
{
    struct ping_stats p;
    double plot[HIST];
    int i, x0 = 126, gw = CW - x0 - 14, gh = 44, gy = 9;
    double top = 0;
    uint16_t col;

    if (load_ping(&p) != 0) { no_data("INTERNET", "waiting for be3600-widgetd"); return; }
    if (p.stale) { no_data("INTERNET", "helper is not updating"); return; }
    col = p.offline ? C_RED : p.last > 150 ? C_ORANGE : C_GREEN;
    cv_dot(18.0f, 15.0f, 4.0f, col);
    ut(T_MB, 28, 10, p.offline ? "Offline" : p.last >= 0 ? "Online" : "Checking", col);
    if (p.last >= 0) {
        char n[16];

        snprintf(n, sizeof n, "%d", p.last);
        ut(T_XL, 14, 27, n, C_WHITE);
        ut(T_S, 14 + ut_w(T_XL, n) + 4, 41, "ms", C_GRAY);
    }
    ut_fit(T_S, 14, 60, 100, p.target, C_TERT);
    cv_rect(114, 10, 1, CH - 20, C_DIM);
    for (i = 0; i < p.n; i++) { plot[i] = p.v[i] < 0 ? 0 : p.v[i]; if (plot[i] > top) top = plot[i]; }
    if (top < 50) top = 50;
    cv_rect(x0, gy + gh, gw, 1, C_DIM);
    cv_graph(x0, gy, gw, gh, plot, p.n, top, col, 1);
    for (i = 0; i < p.n; i++)                                                     /* a red dot where a reply was lost */
        if (p.v[i] < 0) cv_dot((float)(x0 + (p.n > 1 ? i * (gw - 1) / (p.n - 1) : 0)), (float)(gy + gh) - 2.0f, 2.0f, C_RED);
    ut_f(T_XS, x0, 61, C_TERT, "AVG %.0f MS", p.avg);
    { char l[24]; snprintf(l, sizeof l, "LOSS %.0f%%", p.loss); ut_r(T_XS, CW - 14, 61, l, p.loss > 5 ? C_RED : p.loss > 0 ? C_ORANGE : C_TERT); }
}


/* ---------------------------------------------------------------- data usage (today, this month, a cap) */
static double usage_day_rx, usage_day_tx;              /* today */
static double usage_mon_rx, usage_mon_tx;              /* this month */
static unsigned long long usage_last_rx, usage_last_tx;
static int usage_day = -1, usage_month = -1, usage_ready;
static long long usage_saved_ms;
static double cfg_cap_gb;                              /* DATA_CAP_GB, 0 = no cap */

static void usage_month_path(char *out, size_t cap) { rp(out, cap, "/etc/be3600-screen/usage-month"); }

static void usage_save(void)
{
    char p[300], tmp[320];
    FILE *f;

    if (!usage_ready || usage_month < 0) return;
    usage_month_path(p, sizeof p);
    snprintf(tmp, sizeof tmp, "%s.new", p);
    f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "%d %.0f %.0f %llu %llu\n", usage_month, usage_mon_rx, usage_mon_tx, usage_last_rx, usage_last_tx);
    if (fclose(f) == 0) rename(tmp, p); else unlink(tmp);
    usage_saved_ms = mono_ms();
}

static void usage_update(void)
{
    char p[300], b[200];
    int ymd = (W_tm.tm_year + 1900) * 10000 + (W_tm.tm_mon + 1) * 100 + W_tm.tm_mday, ym = ymd / 100;
    double drx, dtx;

    if (!s_have_net) return;
    if (!usage_ready) {
        int m = 0;
        double mr = 0, mt = 0;
        unsigned long long lr = 0, lt = 0;

        usage_month_path(p, sizeof p);
        if (slurp(p, b, sizeof b) > 0 && sscanf(b, "%d %lf %lf %llu %llu", &m, &mr, &mt, &lr, &lt) == 5 && m == ym) {
            usage_mon_rx = mr; usage_mon_tx = mt; usage_last_rx = lr; usage_last_tx = lt;
        } else { usage_mon_rx = usage_mon_tx = 0; usage_last_rx = net_rx; usage_last_tx = net_tx; }
        usage_month = ym;
        usage_ready = 1;
    }
    /* Whatever came in since the last look; a counter that went down means the router restarted, so it began again at 0. */
    drx = net_rx >= usage_last_rx ? (double)(net_rx - usage_last_rx) : (double)net_rx;
    dtx = net_tx >= usage_last_tx ? (double)(net_tx - usage_last_tx) : (double)net_tx;
    usage_last_rx = net_rx; usage_last_tx = net_tx;
    if (ym != usage_month) { usage_month = ym; usage_mon_rx = usage_mon_tx = 0; }
    usage_mon_rx += drx; usage_mon_tx += dtx;
    if (ymd != usage_day) {
        if (usage_day >= 0) usage_day_rx = usage_day_tx = 0;
        else { usage_day_rx = usage_day_tx = 0; }
        usage_day = ymd;
    }
    usage_day_rx += drx; usage_day_tx += dtx;
    if (mono_ms() - usage_saved_ms > 30LL * 60 * 1000) usage_save();
}

static void arrow(float x, float y, int down, uint16_t c)
{
    if (down) { cv_line(x, y, x, y + 8, 1.7f, c); cv_line(x - 3.5f, y + 5, x, y + 9, 1.7f, c); cv_line(x + 3.5f, y + 5, x, y + 9, 1.7f, c); }
    else { cv_line(x, y + 9, x, y + 1, 1.7f, c); cv_line(x - 3.5f, y + 4, x, y, 1.7f, c); cv_line(x + 3.5f, y + 4, x, y, 1.7f, c); }
}

static void usage_block(int x, const char *label, double rx, double tx)
{
    char a[24], b[24];

    ut_label(x, 8, label, C_GRAY);
    fmt_bytes(rx, a, sizeof a);
    fmt_bytes(tx, b, sizeof b);
    arrow((float)x + 4.0f, 21.0f, 1, C_GREEN);
    ut(T_L, x + 13, 20, a, C_WHITE);
    arrow((float)x + 4.0f, 42.0f, 0, C_BLUE);
    ut(T_M, x + 13, 42, b, C_GRAY);
}

static void w_usage(void)
{
    char a[24];

    if (!usage_ready) { no_data("DATA USED", "counting starts now"); return; }
    usage_block(14, "Today", usage_day_rx, usage_day_tx);
    cv_rect(140, 10, 1, 42, C_DIM);
    usage_block(152, "This month", usage_mon_rx, usage_mon_tx);
    if (cfg_cap_gb > 0) {
        double used = usage_mon_rx + usage_mon_tx, pct = used * 100.0 / (cfg_cap_gb * 1073741824.0);
        char t[40];

        cv_capsule(14, 62, CW - 128, 6, pct, level_color(pct, 80, 100));
        snprintf(t, sizeof t, "%.0f%% of %.0f GB", pct, cfg_cap_gb);
        ut_r(T_S, CW - 14, 60, t, pct >= 100 ? C_RED : C_GRAY);
    } else {
        fmt_bytes(usage_mon_rx + usage_mon_tx, a, sizeof a);
        ut_f(T_S, 14, 59, C_TERT, "%s this month  -  set DATA_CAP_GB for a cap", a);
    }
}


/* ---------------------------------------------------------------- page: VPN */
static int load_vpn(char names[][24], int *up, long *age, int max)
{
    char b[1024], *line, *save = NULL;
    int n = 0;

    if (data_file("vpn.txt", b, sizeof b) <= 0 || file_age_s("vpn.txt") > 90) return -1;
    for (line = strtok_r(b, "\n", &save); line && n < max; line = strtok_r(NULL, "\n", &save)) {
        char nm[32] = "", st[16] = "";
        long a = -1;

        if (sscanf(line, "%31[^|]|%15[^|]|%ld", nm, st, &a) >= 2) {
            snprintf(names[n], 24, "%s", nm);
            up[n] = strcmp(st, "up") == 0;
            age[n] = a;
            n++;
        }
    }
    return n;
}

static void w_vpn(void)
{
    char names[4][24];
    int up[4], n, i;
    long age[4];

    n = load_vpn(names, up, age, 4);
    ut(T_MB, 14, 8, "VPN", C_WHITE);
    if (n < 0) { no_data("VPN", "waiting for be3600-widgetd"); return; }
    if (n == 0) { ut(T_M, 14, 38, "No VPN is set up", C_GRAY); return; }
    for (i = 0; i < n; i++) {
        int y = 24 + i * 17;
        char t[40] = "";

        cv_dot(19.0f, (float)y + 6.0f, 4.0f, up[i] ? C_GREEN : C_RED);
        ut_fit(T_M, 32, y + 1, 130, names[i], C_WHITE);
        if (up[i] && age[i] >= 0) { human_dur(age[i], t, sizeof t); ut_f(T_S, CW - 14 - 100, y + 3, C_GRAY, "handshake %s ago", t); }
        else ut_r(T_S, CW - 14, y + 3, up[i] ? "Connected" : "Down", up[i] ? C_GREEN : C_RED);
    }
}


/* ---------------------------------------------------------------- page: overall health */
static void w_health(void)
{
    struct ping_stats p;
    char issue[6][80];
    int n = 0, worst = 0, i;
    uint16_t col;
    char vn[4][24];
    int vu[4], vn_n;
    long va[4];

#define ISSUE(level, ...) do { if (n < 6) { snprintf(issue[n], sizeof issue[n], __VA_ARGS__); if ((level) > worst) worst = (level); n++; } } while (0)

    if (load_ping(&p) == 0 && !p.stale) {
        if (p.offline) ISSUE(2, "Internet is down");
        else if (p.loss > 20) ISSUE(1, "Packet loss %.0f%%", p.loss);
        else if (p.last > 200) ISSUE(1, "Slow internet: %d ms", p.last);
    } else if (!s_have_net) ISSUE(2, "No internet link");
    if (s_temp >= 85) ISSUE(2, "Too hot: %.0f C", s_temp);
    else if (s_temp >= 70) ISSUE(1, "Warm: %.0f C", s_temp);
    if (s_mem >= 92) ISSUE(2, "Memory %.0f%% full", s_mem);
    else if (s_mem >= 85) ISSUE(1, "Memory %.0f%% used", s_mem);
    if (s_flash >= 92) ISSUE(2, "Storage %.0f%% full", s_flash);
    else if (s_flash >= 80) ISSUE(1, "Storage %.0f%% used", s_flash);
    vn_n = load_vpn(vn, vu, va, 4);
    for (i = 0; i < vn_n; i++) if (!vu[i]) ISSUE(1, "VPN %.20s is down", vn[i]);
    if (cfg_cap_gb > 0 && usage_ready) {
        double pct = (usage_mon_rx + usage_mon_tx) * 100.0 / (cfg_cap_gb * 1073741824.0);

        if (pct >= 100) ISSUE(2, "Data cap reached");
        else if (pct >= 80) ISSUE(1, "Data cap %.0f%% used", pct);
    }
    col = worst == 2 ? C_RED : worst == 1 ? C_ORANGE : C_GREEN;
    cv_dot(40.0f, 38.0f, 25.0f, col);
    if (worst == 0) {                                   /* a check mark */
        cv_line(28, 39, 36, 47, 4.2f, C_WHITE);
        cv_line(36, 47, 53, 28, 4.2f, C_WHITE);
    } else {                                            /* an exclamation mark */
        cv_line(40, 27, 40, 40, 4.4f, C_WHITE);
        cv_dot(40.0f, 49.0f, 2.6f, C_WHITE);
    }
    if (n == 0) {
        ut(T_L, 82, 14, "All good", C_WHITE);
        ut(T_S, 82, 44, "Internet, heat, memory, storage", C_GRAY);
        ut(T_S, 82, 57, "and VPN are all fine.", C_GRAY);
        return;
    }
    ut(T_L, 82, 12, worst == 2 ? "Needs attention" : "Worth a look", C_WHITE);
    for (i = 0; i < n && i < 2; i++) ut_fit(i == 0 ? T_M : T_S, 82, i == 0 ? 40 : 57, CW - 96, issue[i], i == 0 ? C_GRAY : C_TERT);
    if (n > 2) ut_f(T_XS, 82, 66, C_TERT, "+%d MORE", n - 2);
#undef ISSUE
}
