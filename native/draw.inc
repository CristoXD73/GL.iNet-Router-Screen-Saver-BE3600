/*
 * draw.inc -- the drawing layer of the screen pages: a 284 x 76 canvas (the strip as you look at it: x to the right,
 * y down) with anti-aliased shapes, rounded cards, pills, activity rings, gradient graphs and smooth proportional
 * text. The canvas is turned into the display's own layout by cv_to_frame(). Included by sample.inc.
 */

#include <ctype.h>

#define CW 284
#define CH 76
#define HIST_MAX 128

/* ---------------------------------------------------------------- colours (a dark, Apple-like palette) */
static uint16_t rgb(unsigned r, unsigned g, unsigned b) { return (uint16_t)(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)); }

#define C_BLACK  0x0000
#define C_WHITE  0xFFFF
#define C_GRAY   rgb(142, 142, 147)      /* secondary label */
#define C_TERT   rgb(99, 99, 102)        /* tertiary label */
#define C_DIM    rgb(44, 44, 46)         /* separators, empty tracks */
#define C_CARD   rgb(28, 28, 30)
#define C_GREEN  rgb(48, 209, 88)
#define C_RED    rgb(255, 69, 58)
#define C_BLUE   rgb(10, 132, 255)
#define C_ORANGE rgb(255, 159, 10)
#define C_YELLOW rgb(255, 214, 10)
#define C_CYAN   rgb(100, 210, 255)
#define C_PURPLE rgb(191, 90, 242)

static uint16_t cv[CH][CW];
static uint8_t mk[CH][CW];              /* coverage of the shape being built (0..255); mk_flush() paints it */
static int mk_x0 = CW, mk_x1 = -1, mk_y0 = CH, mk_y1 = -1;
static int clip_x0 = 0, clip_x1 = CW;        /* drawing is limited to these columns (for text that scrolls under an edge) */

static void cv_fill(uint16_t c)
{
    int x, y;

    for (y = 0; y < CH; y++)
        for (x = 0; x < CW; x++) cv[y][x] = c;
}

/* Blends colour c over what is there, a = 0 (nothing) .. 255 (solid). */
static void cv_blend(int x, int y, uint16_t c, int a)
{
    uint16_t o;
    unsigned r0, g0, b0, r1, g1, b1;

    if (a <= 0 || x < clip_x0 || x >= clip_x1 || y < 0 || y >= CH) return;
    if (a >= 255) { cv[y][x] = c; return; }
    o = cv[y][x];
    r0 = (o >> 11) & 31; g0 = (o >> 5) & 63; b0 = o & 31;
    r1 = (c >> 11) & 31; g1 = (c >> 5) & 63; b1 = c & 31;
    cv[y][x] = (uint16_t)((((r0 * (255 - a) + r1 * a) / 255) << 11) | (((g0 * (255 - a) + g1 * a) / 255) << 5) | ((b0 * (255 - a) + b1 * a) / 255));
}

static void cv_rect(int x, int y, int w, int h, uint16_t c)
{
    int i, j;

    for (j = y; j < y + h; j++)
        for (i = x; i < x + w; i++) cv_blend(i, j, c, 255);
}

static void cv_rect_a(int x, int y, int w, int h, uint16_t c, int a)
{
    int i, j;

    for (j = y; j < y + h; j++)
        for (i = x; i < x + w; i++) cv_blend(i, j, c, a);
}

/* ---------------------------------------------------------------- anti-aliased shapes (coverage mask, then paint) */
static float fsq(float x)
{
    float g;
    int i;

    if (x <= 0) return 0;
    g = x > 1 ? x / 2 : 1;
    for (i = 0; i < 8; i++) g = 0.5f * (g + x / g);
    return g;
}

static float fmaxf_(float a, float b) { return a > b ? a : b; }
static float fminf_(float a, float b) { return a < b ? a : b; }
static float fabsf_(float a) { return a < 0 ? -a : a; }

static void mk_set(int x, int y, float cov)
{
    int v;

    if (x < 0 || x >= CW || y < 0 || y >= CH || cov <= 0) return;
    v = cov >= 1 ? 255 : (int)(cov * 255.0f + 0.5f);
    if (v > mk[y][x]) mk[y][x] = (uint8_t)v;
    if (x < mk_x0) mk_x0 = x;
    if (x > mk_x1) mk_x1 = x;
    if (y < mk_y0) mk_y0 = y;
    if (y > mk_y1) mk_y1 = y;
}

static void mk_flush(uint16_t c)
{
    int x, y;

    for (y = mk_y0; y <= mk_y1; y++)
        for (x = mk_x0; x <= mk_x1; x++)
            if (mk[y][x]) { cv_blend(x, y, c, mk[y][x]); mk[y][x] = 0; }
    mk_x0 = CW; mk_x1 = -1; mk_y0 = CH; mk_y1 = -1;
}

static void mk_disc(float cx, float cy, float r)
{
    int x, y;

    for (y = (int)(cy - r - 1); y <= (int)(cy + r + 1); y++)
        for (x = (int)(cx - r - 1); x <= (int)(cx + r + 1); x++) {
            float dx = (float)x + 0.5f - cx, dy = (float)y + 0.5f - cy;

            mk_set(x, y, 0.5f - (fsq(dx * dx + dy * dy) - r));
        }
}

/* A rounded rectangle; r is the corner radius. */
static void mk_rrect(float x, float y, float w, float h, float r)
{
    int px, py;
    float cx = x + w / 2, cy = y + h / 2, hx = w / 2 - r, hy = h / 2 - r;

    if (r > w / 2) r = w / 2;
    if (r > h / 2) r = h / 2;
    hx = w / 2 - r; hy = h / 2 - r;
    for (py = (int)y - 1; py <= (int)(y + h) + 1; py++)
        for (px = (int)x - 1; px <= (int)(x + w) + 1; px++) {
            float qx = fabsf_((float)px + 0.5f - cx) - hx, qy = fabsf_((float)py + 0.5f - cy) - hy;
            float ox = fmaxf_(qx, 0), oy = fmaxf_(qy, 0);
            float d = fsq(ox * ox + oy * oy) + fminf_(fmaxf_(qx, qy), 0) - r;

            mk_set(px, py, 0.5f - d);
        }
}

/* A line of width w with round ends. */
static void mk_line(float x0, float y0, float x1, float y1, float w)
{
    int x, y;
    float dx = x1 - x0, dy = y1 - y0, l2 = dx * dx + dy * dy, r = w / 2;
    int bx0 = (int)(fminf_(x0, x1) - r - 1), bx1 = (int)(fmaxf_(x0, x1) + r + 1), by0 = (int)(fminf_(y0, y1) - r - 1), by1 = (int)(fmaxf_(y0, y1) + r + 1);

    for (y = by0; y <= by1; y++)
        for (x = bx0; x <= bx1; x++) {
            float px = (float)x + 0.5f - x0, py = (float)y + 0.5f - y0, t = l2 > 0 ? (px * dx + py * dy) / l2 : 0, ex, ey;

            if (t < 0) t = 0;
            if (t > 1) t = 1;
            ex = px - t * dx; ey = py - t * dy;
            mk_set(x, y, 0.5f - (fsq(ex * ex + ey * ey) - r));
        }
}

/* sin and cos of whole degrees, built by rotating a vector (no maths library needed) */
static float ctab[361], stab[361];
static int trig_ready;

static void trig_init(void)
{
    const float c1 = 0.99984769f, s1 = 0.01745241f;
    float c = 1, s = 0;
    int i;

    if (trig_ready) return;
    for (i = 0; i <= 360; i++) {
        ctab[i] = c; stab[i] = s;
        { float nc = c * c1 - s * s1, ns = s * c1 + c * s1; c = nc; s = ns; }
    }
    trig_ready = 1;
}

/* sin and cos of any angle in degrees (0 = up is handled by the callers), by interpolating the table. */
static float fsin_d(float deg)
{
    int i;
    float f;

    trig_init();
    while (deg < 0) deg += 360.0f;
    while (deg >= 360.0f) deg -= 360.0f;
    i = (int)deg;
    f = deg - (float)i;
    return stab[i] + (stab[i + 1] - stab[i]) * f;
}

static float fcos_d(float deg)
{
    int i;
    float f;

    trig_init();
    while (deg < 0) deg += 360.0f;
    while (deg >= 360.0f) deg -= 360.0f;
    i = (int)deg;
    f = deg - (float)i;
    return ctab[i] + (ctab[i + 1] - ctab[i]) * f;
}
/* An arc of a ring: from degrees a0 to a1, clockwise, 0 = straight up, with round ends. */
static void mk_arc(float cx, float cy, float r, float thick, float a0, float a1)
{
    float len = (a1 - a0) * 3.14159265f / 180.0f * r, step;
    int n, i;

    trig_init();
    if (a1 <= a0) return;
    n = (int)(len / 0.35f) + 2;
    step = (a1 - a0) / (float)(n - 1);
    for (i = 0; i < n; i++) {
        float a = a0 + step * (float)i;
        int d = (int)(a + 0.5f);
        float ca, sa;

        while (d < 0) d += 360;
        d %= 360;
        ca = ctab[d]; sa = stab[d];
        mk_disc(cx + r * sa, cy - r * ca, thick / 2);
    }
}

/* The same, but the whole shape at a fraction of full strength (for fading tails and glows). */
static void mk_flush_a(uint16_t c, int alpha)
{
    int x, y;

    if (alpha > 255) alpha = 255;
    for (y = mk_y0; y <= mk_y1; y++)
        for (x = mk_x0; x <= mk_x1; x++)
            if (mk[y][x]) { cv_blend(x, y, c, mk[y][x] * alpha / 255); mk[y][x] = 0; }
    mk_x0 = CW; mk_x1 = -1; mk_y0 = CH; mk_y1 = -1;
}

/* A vertical gradient. The display only has 5 or 6 bits per colour, so the steps are dithered with an ordered
 * 4x4 pattern; without it a slow fade shows as visible bands. */
static void cv_vgrad(int y0, int y1, uint16_t top, uint16_t bot)
{
    static const int bayer[4][4] = { { 0, 8, 2, 10 }, { 12, 4, 14, 6 }, { 3, 11, 1, 9 }, { 15, 7, 13, 5 } };
    int x, y;
    int r0 = (top >> 11) & 31, g0 = (top >> 5) & 63, b0 = top & 31;
    int r1 = (bot >> 11) & 31, g1 = (bot >> 5) & 63, b1 = bot & 31;
    int span = y1 - y0 > 1 ? y1 - y0 - 1 : 1;

    for (y = y0; y < y1; y++) {
        int t = (y - y0) * 256 / span;
        int r = r0 * (256 - t) + r1 * t, g = g0 * (256 - t) + g1 * t, b = b0 * (256 - t) + b1 * t;

        for (x = 0; x < CW; x++) {
            int d = bayer[y & 3][x & 3] * 16;
            int rr = (r + d) >> 8, gg = (g + d) >> 8, bb = (b + d) >> 8;

            if (rr > 31) rr = 31;
            if (gg > 63) gg = 63;
            if (bb > 31) bb = 31;
            cv[y][x] = (uint16_t)((rr << 11) | (gg << 5) | bb);
        }
    }
}
/* ---------------------------------------------------------------- shapes, painted */
static void cv_dot(float cx, float cy, float r, uint16_t c) { mk_disc(cx, cy, r); mk_flush(c); }
static void cv_card(float x, float y, float w, float h, float r, uint16_t c) { mk_rrect(x, y, w, h, r); mk_flush(c); }
static void cv_pill(float x, float y, float w, float h, uint16_t c) { mk_rrect(x, y, w, h, h / 2); mk_flush(c); }
static void cv_line(float x0, float y0, float x1, float y1, float w, uint16_t c) { mk_line(x0, y0, x1, y1, w); mk_flush(c); }

/* A capsule progress bar: an empty track with a rounded fill of pct percent. */
static void cv_capsule(float x, float y, float w, float h, double pct, uint16_t fg)
{
    float fw;

    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    cv_pill(x, y, w, h, C_DIM);
    fw = (float)pct * w / 100.0f;
    if (fw < h && pct > 0) fw = h;
    if (pct > 0) cv_pill(x, y, fw, h, fg);
}

/* An activity ring: a full dim track and a coloured arc for pct percent, starting at the top. */
static void cv_ring(float cx, float cy, float r, float thick, double pct, uint16_t fg)
{
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    mk_arc(cx, cy, r, thick, 0, 359.5f);
    mk_flush(C_DIM);
    if (pct > 0.5) { mk_arc(cx, cy, r, thick, 0, (float)pct * 3.595f); mk_flush(fg); }
}

static uint16_t level_color(double pct, double warn, double bad) { return pct >= bad ? C_RED : pct >= warn ? C_ORANGE : C_GREEN; }

/* A line graph of n samples with a soft gradient underneath. Returns the scale used. */
static double cv_graph(int x, int y, int w, int h, const double *v, int n, double max, uint16_t c, int fill)
{
    int i, col;
    float py[HIST_MAX];
    float top;

    if (n > HIST_MAX) n = HIST_MAX;
    if (max <= 0) {
        for (i = 0; i < n; i++) if (v[i] > max) max = v[i];
        if (max <= 0) max = 1;
    }
    for (i = 0; i < n; i++) {
        double val = v[i] < 0 ? 0 : v[i];

        py[i] = (float)y + (float)h - 2 - (float)(val / max) * (float)(h - 4);
        if (py[i] < (float)y + 1) py[i] = (float)y + 1;
    }
    if (fill && n > 1) {
        for (col = 0; col < w; col++) {
            float fx = (float)col * (float)(n - 1) / (float)(w - 1), yy;
            int i0 = (int)fx, yi;

            if (i0 >= n - 1) i0 = n - 2;
            yy = py[i0] + (py[i0 + 1] - py[i0]) * (fx - (float)i0);
            top = yy;
            for (yi = (int)top; yi < y + h; yi++) {
                float k = 1.0f - ((float)yi - top) / ((float)y + (float)h - top + 1);

                cv_blend(x + col, yi, c, (int)(70.0f * k * k));
            }
        }
    }
    for (i = 0; i + 1 < n; i++)
        mk_line((float)x + (float)i * (float)(w - 1) / (float)(n - 1), py[i], (float)x + (float)(i + 1) * (float)(w - 1) / (float)(n - 1), py[i + 1], 1.8f);
    mk_flush(c);
    return max;
}

/* The canvas in the display's layout: image x runs down the display's rows, image y along its columns. */
static void cv_to_frame(uint8_t *dst)
{
    int x, y;

    for (y = 0; y < CH; y++)
        for (x = 0; x < CW; x++) {
            size_t i = (size_t)(CW - 1 - x) * ROW_BYTES + (size_t)y * 2;

            dst[i] = (uint8_t)(cv[y][x] & 255);
            dst[i + 1] = (uint8_t)(cv[y][x] >> 8);
        }
}

/* ---------------------------------------------------------------- text: smooth, proportional */
struct uglyph { uint8_t w, h; int8_t bx, by; uint8_t adv; uint16_t off; };
struct uface { int cap, px, n, full, first; const struct uglyph *g; const uint8_t *data; };

#include "uifont.inc"

enum { T_XS, T_S, T_M, T_MB, T_L, T_XL, T_XXL };

static const struct uglyph *ut_glyph(int f, unsigned ch)
{
    const struct uface *fc = &uifaces[f];

    if (fc->full) {
        if (ch < 32 || ch > 126) ch = '?';
        return &fc->g[ch - 32];
    }
    {
        const char *p = ch ? strchr(ui_numchars, (int)ch) : NULL;

        return p ? &fc->g[p - ui_numchars] : NULL;
    }
}

static int ut_w(int f, const char *s)
{
    int w = 0;

    for (; *s; s++) {
        const struct uglyph *g = ut_glyph(f, (unsigned char)*s);

        w += g ? g->adv : uifaces[f].px / 3;
    }
    return w;
}

/* Draws s with the top of its capital letters at y; sp is extra space between letters. Returns the width. */
static int ut_sp(int f, int x, int y, const char *s, uint16_t c, int sp)
{
    const struct uface *fc = &uifaces[f];
    int x0 = x, base = y + fc->cap;

    for (; *s; s++) {
        const struct uglyph *g = ut_glyph(f, (unsigned char)*s);
        int row, col, gx;

        if (!g) { x += fc->px / 3 + sp; continue; }
        if (*s >= '0' && *s <= '9') gx = (g->adv - g->w) / 2 - g->bx;      /* digits are centred in their equal-width cells */
        else gx = 0;
        for (row = 0; row < g->h; row++)
            for (col = 0; col < g->w; col++) {
                int a = fc->data[g->off + row * g->w + col];

                if (a) cv_blend(x + gx + g->bx + col, base + g->by + row, c, a);
            }
        x += g->adv + sp;
    }
    return x - x0;
}

static int ut(int f, int x, int y, const char *s, uint16_t c) { return ut_sp(f, x, y, s, c, 0); }
static void ut_r(int f, int xr, int y, const char *s, uint16_t c) { ut(f, xr - ut_w(f, s), y, s, c); }
static void ut_c(int f, int xc, int y, const char *s, uint16_t c) { ut(f, xc - ut_w(f, s) / 2, y, s, c); }

static void ut_f(int f, int x, int y, uint16_t c, const char *fmt, ...)
{
    char b[128];
    va_list ap;

    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    ut(f, x, y, b, c);
}

/* Small capital label, letter-spaced (the grey captions above values). */
static int ut_label(int x, int y, const char *s, uint16_t c)
{
    char b[64];
    int i;

    for (i = 0; s[i] && i < 62; i++) b[i] = (char)toupper((unsigned char)s[i]);
    b[i] = 0;
    return ut_sp(T_XS, x, y, b, c, 1);
}

/* Text cut to fit w pixels (adds ".." if it had to cut). */
static void ut_fit(int f, int x, int y, int w, const char *s, uint16_t c)
{
    char b[96];
    size_t n;

    snprintf(b, sizeof b, "%s", s);
    if (ut_w(f, b) > w) {
        n = strlen(b);
        while (n > 1 && ut_w(f, b) + ut_w(f, "..") > w) b[--n] = 0;
        strcat(b, "..");
    }
    ut(f, x, y, b, c);
}

static int ut_is_num(const char *s)      /* only characters the big number faces hold */
{
    for (; *s; s++) if (!strchr(ui_numchars, *s)) return 0;
    return 1;
}
