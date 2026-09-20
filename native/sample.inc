/*
 * sample.inc -- the screen widgets of be3600-player (included by be3600-player.c, one translation unit).
 *
 * Widgets draw on a 284 x 76 canvas in the orientation you look at the strip (x to the right, y down); the
 * canvas is turned into the display's own layout when it is sent to the screen. Live numbers come from /proc and
 * /sys (read here once a second) and from small text files the background helper be3600-widgetd writes into the
 * data directory (ping history, Wi-Fi clients, VPN state, weather, ...).
 *
 * Nothing here runs unless a page that needs it is switched on in the config (PAGES).
 */

#include <ifaddrs.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <stdarg.h>
#include <sys/statvfs.h>
#include <sys/wait.h>
#include <ctype.h>


#define CW 284
#define CH 76

/* ---------------------------------------------------------------- test root */
/* The kernel's files are read relative to this prefix; it is empty except when BE3600_TESTING points it at a fake tree. */
static const char *troot = "";
static const char *ddir = "/tmp/be3600-widgets";

static void rp(char *out, size_t cap, const char *path) { snprintf(out, cap, "%s%s", troot, path); }

static int slurp(const char *path, char *buf, size_t cap)
{
    FILE *f = fopen(path, "rb");
    size_t n;

    if (!f) return -1;
    n = fread(buf, 1, cap - 1, f);
    fclose(f);
    buf[n] = 0;
    return (int)n;
}

static int slurp_rp(const char *path, char *buf, size_t cap)
{
    char p[512];

    rp(p, sizeof p, path);
    return slurp(p, buf, cap);
}

static int data_file(const char *name, char *buf, size_t cap)
{
    char p[600];

    snprintf(p, sizeof p, "%s/%s", ddir, name);
    return slurp(p, buf, cap);
}

#define HIST 60
#include "draw.inc"

/* ---------------------------------------------------------------- formatting */
static void fmt_bytes(double n, char *out, size_t cap)
{
    if (n >= 1099511627776.0) snprintf(out, cap, "%.1f TB", n / 1099511627776.0);
    else if (n >= 1073741824.0) snprintf(out, cap, "%.1f GB", n / 1073741824.0);
    else if (n >= 1048576.0) snprintf(out, cap, "%.0f MB", n / 1048576.0);
    else if (n >= 1024.0) snprintf(out, cap, "%.0f KB", n / 1024.0);
    else snprintf(out, cap, "%.0f B", n);
}

static int speed_bits = 1;    /* SPEED_UNITS=bits (default) or bytes */

static void fmt_rate(double bps, char *out, size_t cap)
{
    double v = speed_bits ? bps * 8.0 : bps;
    const char *u = speed_bits ? "b/s" : "B/s";
    char scale = 0;

    if (v >= 1e9) { v /= 1e9; scale = 'G'; }
    else if (v >= 1e6) { v /= 1e6; scale = 'M'; }
    else if (v >= 1e3) { v /= 1e3; scale = 'K'; }
    if (scale && v < 100) snprintf(out, cap, "%.1f %c%s", v, scale, u);
    else snprintf(out, cap, "%.0f %s%s", v, scale ? (char[2]){ scale, 0 } : "", u);
}

/* ---------------------------------------------------------------- time */
static struct tm W_tm;
static time_t W_t, fake_now;         /* fake_now: a fixed clock, for tests only */
static long long W_ms;              /* monotonic milliseconds */
static int cfg_24h = 1;
static const char *mon[] = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };

static void w_time_update(void)
{
    W_t = fake_now ? fake_now : time(NULL);
    localtime_r(&W_t, &W_tm);
    W_ms = fake_now ? 0 : mono_ms();       /* a fixed clock also stops anything that moves (tests) */
}

/* ---------------------------------------------------------------- live system numbers */
static double h_down[HIST], h_up[HIST], h_cpu[HIST];
static int h_n;
static double s_cpu, s_mem, s_flash, s_temp, s_load1, s_uptime;
static double s_down, s_up;                  /* bytes per second, now */
static unsigned long long net_rx, net_tx;    /* counters of the WAN interface */
static char wan_if[32];
static int s_have_net;
static unsigned long long cpu_busy0, cpu_total0;
static long long last_sample_ms;

static void push_hist(double *h, double v)
{
    memmove(h, h + 1, (HIST - 1) * sizeof *h);
    h[HIST - 1] = v;
}

/* The interface the default route goes out of (the lowest metric wins). */
static void find_wan(void)
{
    char b[4096], *line, *save = NULL;
    unsigned best = 0xffffffffu;

    wan_if[0] = 0;
    if (slurp_rp("/proc/net/route", b, sizeof b) <= 0) return;
    for (line = strtok_r(b, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char ifn[32];
        unsigned dest, flags, metric;
        long gw;

        if (sscanf(line, "%31s %x %lx %x %*d %*d %u", ifn, &dest, &gw, &flags, &metric) == 5 && dest == 0 && (flags & 2) && metric < best) {
            best = metric;
            snprintf(wan_if, sizeof wan_if, "%s", ifn);
        }
    }
}

static int read_net_counters(const char *ifn, unsigned long long *rx, unsigned long long *tx)
{
    char b[8192], *line, *save = NULL;

    if (!*ifn || slurp_rp("/proc/net/dev", b, sizeof b) <= 0) return -1;
    for (line = strtok_r(b, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char name[48];
        unsigned long long v[16];
        char *colon = strchr(line, ':');
        size_t len;

        if (!colon) continue;
        while (*line == ' ') line++;
        len = (size_t)(colon - line);
        if (len == 0 || len >= sizeof name) continue;
        memcpy(name, line, len);
        name[len] = 0;
        if (strcmp(name, ifn) != 0) continue;
        if (sscanf(colon + 1, "%llu %*u %*u %*u %*u %*u %*u %*u %llu", &v[0], &v[8]) == 2) { *rx = v[0]; *tx = v[8]; return 0; }
    }
    return -1;
}

static double read_temp_c(void)
{
    double best = -1;
    int i;

    for (i = 0; i < 16; i++) {
        char p[96], b[32];

        snprintf(p, sizeof p, "/sys/class/thermal/thermal_zone%d/temp", i);
        if (slurp_rp(p, b, sizeof b) > 0) {
            double t = atof(b);

            if (t > 1000) t /= 1000.0;
            if (t > best) best = t;
        }
    }
    return best;
}

/* Called about once a second while the player runs, whatever page is showing, so the graphs have history. */
static void sample_tick(void)
{
    char b[4096];
    long long now = mono_ms();
    double dt = last_sample_ms ? (double)(now - last_sample_ms) / 1000.0 : 0;
    unsigned long long user, nice, sys, idle, iow, irq, sirq, steal, rx, tx;
    struct statvfs sv;
    char p[256];

    if (last_sample_ms && dt < 0.5) return;
    last_sample_ms = now;

    if (slurp_rp("/proc/stat", b, sizeof b) > 0 &&
        sscanf(b, "cpu %llu %llu %llu %llu %llu %llu %llu %llu", &user, &nice, &sys, &idle, &iow, &irq, &sirq, &steal) == 8) {
        unsigned long long busy = user + nice + sys + irq + sirq + steal, total = busy + idle + iow;

        if (cpu_total0 && total > cpu_total0) s_cpu = 100.0 * (double)(busy - cpu_busy0) / (double)(total - cpu_total0);
        cpu_busy0 = busy; cpu_total0 = total;
    }
    if (slurp_rp("/proc/meminfo", b, sizeof b) > 0) {
        double tot = 0, avail = 0;
        char *q = strstr(b, "MemTotal:"), *a = strstr(b, "MemAvailable:");

        if (q) tot = atof(q + 9);
        if (a) avail = atof(a + 13);
        if (tot > 0) s_mem = 100.0 * (1.0 - avail / tot);
    }
    rp(p, sizeof p, "/overlay");
    if (statvfs(p, &sv) != 0) { rp(p, sizeof p, "/"); if (statvfs(p, &sv) != 0) sv.f_blocks = 0; }
    if (sv.f_blocks > 0) s_flash = 100.0 * (1.0 - (double)sv.f_bavail / (double)sv.f_blocks);
    s_temp = read_temp_c();
    if (slurp_rp("/proc/loadavg", b, sizeof b) > 0) s_load1 = atof(b);
    if (slurp_rp("/proc/uptime", b, sizeof b) > 0) s_uptime = atof(b);

    find_wan();
    if (read_net_counters(wan_if, &rx, &tx) == 0) {
        if (s_have_net && dt > 0 && rx >= net_rx && tx >= net_tx) {
            s_down = (double)(rx - net_rx) / dt;
            s_up = (double)(tx - net_tx) / dt;
        } else { s_down = s_up = 0; }
        net_rx = rx; net_tx = tx; s_have_net = 1;
    } else { s_have_net = 0; s_down = s_up = 0; }
    push_hist(h_down, s_down);
    push_hist(h_up, s_up);
    push_hist(h_cpu, s_cpu);
    if (h_n < HIST) h_n++;
}

/* ---------------------------------------------------------------- small helpers for the pages */
static int ip_of(const char *ifn, char *out, size_t cap)
{
    struct ifaddrs *ifa, *p;
    int ok = -1;

    out[0] = 0;
    if (!*ifn || getifaddrs(&ifa) != 0) return -1;
    for (p = ifa; p; p = p->ifa_next)
        if (p->ifa_addr && p->ifa_addr->sa_family == AF_INET && strcmp(p->ifa_name, ifn) == 0) {
            inet_ntop(AF_INET, &((struct sockaddr_in *)p->ifa_addr)->sin_addr, out, (socklen_t)cap);
            ok = 0;
            break;
        }
    freeifaddrs(ifa);
    return ok;
}

static void human_dur(long s, char *out, size_t cap)
{
    if (s >= 86400) snprintf(out, cap, "%ldd %02ldh", s / 86400, (s % 86400) / 3600);
    else if (s >= 3600) snprintf(out, cap, "%ldh %02ldm", s / 3600, (s % 3600) / 60);
    else if (s >= 60) snprintf(out, cap, "%ldm %02lds", s / 60, s % 60);
    else snprintf(out, cap, "%lds", s);
}

/* Reads a KEY=value line out of a data file. Returns 1 if found. */
static int kv_get(const char *text, const char *key, char *out, size_t cap)
{
    size_t kl = strlen(key);
    const char *p = text;

    while (p && *p) {
        if (strncmp(p, key, kl) == 0 && p[kl] == '=') {
            size_t n = strcspn(p + kl + 1, "\n");

            if (n >= cap) n = cap - 1;
            memcpy(out, p + kl + 1, n);
            out[n] = 0;
            return 1;
        }
        p = strchr(p, '\n');
        if (p) p++;
    }
    return 0;
}

/* The header every data-file page shows when its data has not arrived (yet). */
static void no_data(const char *title, const char *why)
{
    ut_label(14, 12, title, C_GRAY);
    ut_fit(T_M, 14, 34, CW - 28, why, C_ORANGE);
}

static int file_age_s(const char *name)
{
    char p[600];
    struct stat st;

    snprintf(p, sizeof p, "%s/%s", ddir, name);
    if (stat(p, &st) != 0) return -1;
    return (int)(W_t - st.st_mtime);
}
