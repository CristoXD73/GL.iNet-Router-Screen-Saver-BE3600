/*
 * pages_manage.inc -- the two pages that manage what you put on the router: your animation
 * slots, and your fan chimes. Included by be3600-player.c after pages_touch.inc.
 *
 * Both read a small text file be3600-widgetd keeps in /tmp, and both act through
 * be3600-widget-action, the only program the player ever runs.
 */

static void slots_reload(void);          /* defined once the page list exists */

/* ---------------------------------------------------------------- the fan chimes, to try out */
#define CH_MAX 16
static struct { char name[24], steps[168]; } ch[CH_MAX];
static int ch_n, ch_sel;
static char ch_state[12];                /* ready, quiet, hot, nofan */
static long long ch_read_ms = -1;
static char cfg_taps_chime[24];          /* FAN_CHIME_TAPS: five quick taps play this one */

static void ch_load(void)
{
    char b[2048], *line, *save = NULL;

    if (ch_read_ms >= 0 && W_ms - ch_read_ms < 3000) return;
    ch_read_ms = W_ms;
    ch_n = 0;
    ch_state[0] = 0;
    if (data_file("chimes.txt", b, sizeof b) <= 0) return;
    for (line = strtok_r(b, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char *tab = strchr(line, '\t');

        if (!tab) continue;
        *tab++ = 0;
        if (strcmp(line, "state") == 0) { snprintf(ch_state, sizeof ch_state, "%s", tab); continue; }
        if (ch_n >= CH_MAX) continue;
        snprintf(ch[ch_n].name, sizeof ch[ch_n].name, "%s", line);
        snprintf(ch[ch_n].steps, sizeof ch[ch_n].steps, "%s", tab);
        ch_n++;
    }
    if (ch_sel >= ch_n) ch_sel = 0;
}

/* The shape of a chime: how hard the fan is driven, along the time it takes. The whole width is
 * the twelve seconds a chime is allowed to hold the fan, so a blip looks like a blip next to a
 * rev instead of both filling the strip. Returns the length in milliseconds. */
#define CH_SPAN_MS 12000

static int ch_envelope(int x, int y, int w, int h, const char *steps, uint16_t c)
{
    int duty[32], ms[32], n = 0, total = 0, i, at = 0;
    const char *p = steps;

    while (*p && n < 32) {
        int d = 0, t = 0, used = 0;

        if (sscanf(p, "%d:%d%n", &d, &t, &used) != 2 || used <= 0) break;
        if (d < 0) d = 0;
        if (d > 255) d = 255;
        if (t < 1) t = 1;
        duty[n] = d;
        ms[n] = t;
        total += t;
        n++;
        p += used;
        while (*p == ' ' || *p == '\t') p++;
    }
    if (n == 0) return 0;
    cv_rect(x, y + h - 1, w, 1, C_DIM);
    for (i = 0; i < n; i++) {
        int x0 = x + (int)((long)at * w / CH_SPAN_MS);
        int x1 = x + (int)((long)(at + ms[i]) * w / CH_SPAN_MS);
        int bh = duty[i] * (h - 3) / 255;

        if (x1 > x + w) x1 = x + w;
        if (x1 <= x0) x1 = x0 + 1;
        cv_rect(x0, y + h - 1 - bh, x1 - x0 > 2 ? x1 - x0 - 1 : x1 - x0, bh + 1, c);
        at += ms[i];
    }
    return total;
}

static void w_chimes(void)
{
    int i, x, ms;
    const char *hint;
    uint16_t hc = C_TERT, bar = C_CYAN;
    char t[24];

    ch_load();
    if (strcmp(ch_state, "nofan") == 0) { no_data("FAN CHIMES", "this router has no fan to speak with"); return; }
    if (ch_n == 0) { no_data("FAN CHIMES", "waiting for be3600-widgetd"); return; }

    ut_label(14, 8, "Fan chimes", C_GRAY);
    snprintf(t, sizeof t, "%d OF %d", ch_sel + 1, ch_n);
    ut_r(T_XS, CW - 14, 8, t, C_TERT);
    ut_fit(T_L, 14, 19, CW - 96, ch[ch_sel].name, C_WHITE);
    if (*cfg_taps_chime && strcmp(cfg_taps_chime, ch[ch_sel].name) == 0) {
        cv_pill((float)(CW - 14 - 56), 20, 56, 17, rgb(38, 38, 42));
        ut_c(T_XS, CW - 14 - 28, 25, "FIVE TAPS", C_CYAN);
    }
    if (strcmp(ch_state, "quiet") == 0) { hint = "QUIET HOURS - NOTHING WILL PLAY"; hc = C_ORANGE; bar = C_DIM; }
    else if (strcmp(ch_state, "hot") == 0) { hint = "TOO WARM - THE FAN IS BUSY COOLING"; hc = C_ORANGE; bar = C_DIM; }
    else if (hold_prog > 0) { hint = "KEEP HOLDING TO HEAR IT AGAIN"; hc = C_CYAN; }
    else hint = "TAP FOR THE NEXT ONE, HOLD TO REPEAT";
    ms = ch_envelope(14, 40, CW - 28, 20, ch[ch_sel].steps, bar);
    if (ms > 0) {
        int ex = 14 + (int)((long)ms * (CW - 28) / CH_SPAN_MS);

        snprintf(t, sizeof t, "%d.%d s", ms / 1000, (ms % 1000) / 100);
        if (ex < CW - 46) ut(T_XS, ex + 6, 42, t, C_TERT);
    }
    ut(T_XS, 14, 65, hint, hc);
    x = CW - 14 - (ch_n - 1) * 7;
    for (i = 0; i < ch_n; i++) cv_dot((float)(x + i * 7), 67.0f, 2.4f, i == ch_sel ? C_WHITE : C_DIM);
}

static int ch_tap(void)
{
    ch_load();
    if (ch_n == 0) return 1;
    ch_sel = (ch_sel + 1) % ch_n;
    run_action("chime", ch[ch_sel].name);
    return 1;
}

static int ch_hold(void)
{
    ch_load();
    if (ch_n) run_action("chime", ch[ch_sel].name);
    return 1;
}


/* ---------------------------------------------------------------- your animation slots */
#define SL_MAX 6
static struct { char name[40]; double bytes, secs; int playing; } sl[SL_MAX];
static int sl_n, sl_sel, sl_slots = 3;
static long long sl_read_ms = -1;

/* What this page has deleted. The helper only rewrites its list every half minute, so without
 * this the slot would reappear for that long. A name is forgotten once the list has lost it. */
static char sl_gone[SL_MAX][40];
static int sl_gone_n;

static int sl_removed(const char *name)
{
    int i;

    for (i = 0; i < sl_gone_n; i++)
        if (strcmp(sl_gone[i], name) == 0) return 1;
    return 0;
}

static void sl_load(void)
{
    char b[1200], *line, *save = NULL, still[SL_MAX][40];
    int still_n = 0, i;

    if (sl_read_ms >= 0 && W_ms - sl_read_ms < 3000) return;
    sl_read_ms = W_ms;
    sl_n = 0;
    if (data_file("anims.txt", b, sizeof b) <= 0) return;
    for (line = strtok_r(b, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char mark[12] = "", name[40] = "";
        double bytes = 0, secs = 0;

        if (sscanf(line, "%11[^\t]\t%39[^\t]\t%lf\t%lf", mark, name, &bytes, &secs) < 2) continue;
        if (strcmp(mark, "limits") == 0) { sl_slots = atoi(name); continue; }   /* limits<tab>HOW MANY<tab>SECONDS */
        if (sl_removed(name)) {
            if (still_n < SL_MAX) snprintf(still[still_n++], 40, "%s", name);
            continue;
        }
        if (sl_n >= SL_MAX) continue;
        snprintf(sl[sl_n].name, sizeof sl[sl_n].name, "%s", name);
        sl[sl_n].bytes = bytes;
        sl[sl_n].secs = secs;
        sl[sl_n].playing = mark[0] == '*';
        sl_n++;
    }
    for (i = 0; i < still_n && i < SL_MAX; i++) memcpy(sl_gone[i], still[i], sizeof sl_gone[i]);
    sl_gone_n = still_n;
    if (sl_slots < 1 || sl_slots > SL_MAX) sl_slots = 3;
}

static void w_slots(void)
{
    int n, i, pad = 10, gap = 6, cw, cy = 20, chh = 40;

    sl_load();
    n = sl_n > sl_slots ? sl_n : sl_slots;
    if (n < 1) n = 1;
    if (n > SL_MAX) n = SL_MAX;
    if (sl_sel >= n) sl_sel = 0;
    cw = (CW - 2 * pad - (n - 1) * gap) / n;

    ut_label(14, 7, "Animations", C_GRAY);
    ut_r(T_XS, CW - 14, 7, sl_n ? "TAP TO PICK, HOLD TO REMOVE" : "NOTHING SAVED YET", C_TERT);

    for (i = 0; i < n; i++) {
        int x = pad + i * (cw + gap), sel = i == sl_sel;

        if (i < sl_n) {
            char size[20], line[40];

            cv_card((float)x, (float)cy, (float)cw, (float)chh, 9.0f, sel ? rgb(44, 44, 48) : C_CARD);
            ut_fit(T_S, x + 8, cy + 7, cw - 16, sl[i].name, C_WHITE);
            fmt_bytes(sl[i].bytes, size, sizeof size);
            snprintf(line, sizeof line, "%.1f s   %s", sl[i].secs, size);
            ut_fit(T_XS, x + 8, cy + 20, cw - 16, line, C_GRAY);
            if (sl[i].playing) {
                cv_dot((float)(x + 11), (float)(cy + 32), 2.6f, C_GREEN);
                ut(T_XS, x + 17, cy + 29, "PLAYING", C_GREEN);
            }
        } else {
            cv_card((float)x, (float)cy, (float)cw, (float)chh, 9.0f, sel ? rgb(30, 30, 33) : rgb(20, 20, 22));
            ut_c(T_XS, x + cw / 2, cy + 13, "EMPTY", C_TERT);
            ut_c(T_XS, x + cw / 2, cy + 24, "SLOT", C_TERT);
        }
        if (sel) {                                   /* a hairline around the one a touch would act on */
            cv_rect(x, cy, cw, 1, C_WHITE);
            cv_rect(x, cy + chh - 1, cw, 1, C_WHITE);
            cv_rect(x, cy, 1, chh, C_WHITE);
            cv_rect(x + cw - 1, cy, 1, chh, C_WHITE);
        }
    }

    if (hold_prog > 0 && sl_sel < sl_n) {
        cv_capsule(14, CH - 8, CW - 28, 4, hold_prog, C_RED);
        ut(T_XS, 14, CH - 16, "KEEP HOLDING TO REMOVE IT", C_RED);
    } else if (sl_n == 0) {
        ut(T_XS, 14, CH - 10, "SEND ONE FROM MOTION STUDIO", C_CYAN);
    }
}

static int sl_tap(void)
{
    int n;

    sl_load();
    n = sl_n > sl_slots ? sl_n : sl_slots;
    if (n < 1) return 1;
    sl_sel = (sl_sel + 1) % n;
    return 1;
}

/* Holding a finger on a filled slot deletes that animation. The 0.9 s hold, with its bar
 * filling up, is the whole confirmation: there is no keyboard here to type "yes" on. */
static int sl_hold(void)
{
    char arg[56];
    int i;

    sl_load();
    if (sl_sel >= sl_n) return 1;                    /* an empty slot: nothing to remove */
    snprintf(arg, sizeof arg, "remove:%s", sl[sl_sel].name);
    run_action("anim", arg);            /* no banner: the slot going empty is the answer, and a
                                           banner would cover this page and eat the next touch */
    if (sl_gone_n < SL_MAX) snprintf(sl_gone[sl_gone_n++], 40, "%s", sl[sl_sel].name);
    for (i = sl_sel; i + 1 < sl_n; i++) sl[i] = sl[i + 1];     /* show it gone now, not in half a minute */
    sl_n--;
    sl_read_ms = W_ms;
    slots_reload();
    return 1;
}
