/*
 * pages.inc -- the table of every page: name, what draws it, what a touch does.
 * Included by be3600-player.c last, once every page function exists.
 */

/* ---------------------------------------------------------------- the pages */
struct widget_def {
    const char *name;
    void (*draw)(void);
    int fps;                       /* redraws per second */
    int (*tap)(void);              /* if set, a single tap does this instead of going to the next page */
    int (*hold)(void);             /* if set, holding a finger down does this */
};

static const struct widget_def widget_table[] = {
    { "clock",     w_clock,     1,  NULL,   NULL },
    { "analog",    w_analog,    25, NULL,   NULL },
    { "aurora",    w_aurora,    25, NULL,   NULL },
    { "netspeed",  w_netspeed,  1,  NULL,   NULL },
    { "talkers",   w_talkers,   1,  NULL,   NULL },
    { "vitals",    w_vitals,    1,  NULL,   NULL },
    { "info",      w_info,      1,  NULL,   NULL },
    { "clients",   w_clients,   1,  NULL,   NULL },
    { "internet",  w_internet,  1,  NULL,   NULL },
    { "doctor",    w_doctor,    1,  NULL,   NULL },
    { "usage",     w_usage,     1,  NULL,   NULL },
    { "vpn",       w_vpn,       1,  NULL,   NULL },
    { "health",    w_health,    1,  NULL,   NULL },
    { "pomodoro",  w_pomodoro,  1,  pt_tap, pt_hold },
    { "stopwatch", w_stopwatch, 10, sw_tap, sw_hold },
    { "message",   w_message,   20, NULL,   NULL },
    { "guest",     w_guest,     4,  NULL,   guest_hold },
    { "wifiqr",    w_wifiqr,    1,  NULL,   NULL },
    { "weather",   w_weather,   1,  NULL,   NULL },
    { "custom",    w_custom,    1,  NULL,   NULL },
};
#define N_WIDGETS ((int)(sizeof widget_table / sizeof widget_table[0]))

static int widget_find(const char *name)
{
    int i;

    for (i = 0; i < N_WIDGETS; i++)
        if (strcmp(widget_table[i].name, name) == 0) return i;
    return -1;
}
