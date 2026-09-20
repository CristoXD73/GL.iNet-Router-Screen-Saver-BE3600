/*
 * qr.inc -- a small QR Code encoder (byte mode, error correction L or M, versions 1 to 6) for the "join Wi-Fi" page.
 * Follows ISO/IEC 18004; laid out like Project Nayuki's reference implementation (MIT). Included by be3600-player.c.
 */

#define QR_MAXV 6
#define QR_MAXN (17 + 4 * QR_MAXV)

static uint8_t qr_m[QR_MAXN][QR_MAXN], qr_fn[QR_MAXN][QR_MAXN];
static int qr_size;

static const int qr_ecc_per_block[2][QR_MAXV + 1] = { { 0, 7, 10, 15, 20, 26, 18 }, { 0, 10, 16, 26, 18, 24, 16 } };
static const int qr_blocks[2][QR_MAXV + 1] = { { 0, 1, 1, 1, 1, 1, 2 }, { 0, 1, 1, 1, 2, 2, 4 } };
static const int qr_align[QR_MAXV + 1] = { 0, 0, 18, 22, 26, 30, 34 };   /* the second alignment coordinate (the first is 6) */

static int qr_raw_bytes(int v)
{
    int raw = (16 * v + 128) * v + 64;

    if (v >= 2) { int a = v / 7 + 2; raw -= (25 * a - 10) * a - 55; }
    return raw / 8;
}

static int qr_gf_mul(int x, int y)
{
    int z = 0, i;

    for (i = 7; i >= 0; i--) {
        z = (z << 1) ^ ((z >> 7) * 0x11D);
        z ^= ((y >> i) & 1) * x;
    }
    return z;
}

static void qr_rs(const uint8_t *data, int len, int deg, uint8_t *out)
{
    uint8_t gen[40];
    int i, j;
    int root = 1;

    memset(gen, 0, sizeof gen);
    gen[deg - 1] = 1;
    for (i = 0; i < deg; i++) {
        for (j = 0; j < deg; j++) {
            gen[j] = (uint8_t)qr_gf_mul(gen[j], root);
            if (j + 1 < deg) gen[j] ^= gen[j + 1];
        }
        root = qr_gf_mul(root, 2);
    }
    memset(out, 0, (size_t)deg);
    for (i = 0; i < len; i++) {
        int factor = data[i] ^ out[0];

        memmove(out, out + 1, (size_t)deg - 1);
        out[deg - 1] = 0;
        for (j = 0; j < deg; j++) out[j] ^= (uint8_t)qr_gf_mul(gen[j], factor);
    }
}

static void qr_set(int x, int y, int dark) { qr_m[y][x] = (uint8_t)(dark != 0); qr_fn[y][x] = 1; }

static void qr_finder(int cx, int cy)
{
    int dx, dy;

    for (dy = -4; dy <= 4; dy++)
        for (dx = -4; dx <= 4; dx++) {
            int d = abs(dx) > abs(dy) ? abs(dx) : abs(dy), x = cx + dx, y = cy + dy;

            if (x >= 0 && x < qr_size && y >= 0 && y < qr_size) qr_set(x, y, d != 2 && d != 4);
        }
}

static void qr_alignment(int cx, int cy)
{
    int dx, dy;

    for (dy = -2; dy <= 2; dy++)
        for (dx = -2; dx <= 2; dx++) qr_set(cx + dx, cy + dy, (abs(dx) > abs(dy) ? abs(dx) : abs(dy)) != 1);
}

static void qr_format(int ecl, int mask)
{
    int data = ((ecl == 0 ? 1 : 0) << 3) | mask, rem = data, i, bits;

    for (i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >> 9) * 0x537);
    bits = ((data << 10) | rem) ^ 0x5412;
    for (i = 0; i <= 5; i++) qr_set(8, i, (bits >> i) & 1);
    qr_set(8, 7, (bits >> 6) & 1);
    qr_set(8, 8, (bits >> 7) & 1);
    qr_set(7, 8, (bits >> 8) & 1);
    for (i = 9; i < 15; i++) qr_set(14 - i, 8, (bits >> i) & 1);
    for (i = 0; i < 8; i++) qr_set(qr_size - 1 - i, 8, (bits >> i) & 1);
    for (i = 8; i < 15; i++) qr_set(8, qr_size - 15 + i, (bits >> i) & 1);
    qr_set(8, qr_size - 8, 1);
}

static int qr_mask_bit(int mask, int x, int y)
{
    switch (mask) {
    case 0: return (x + y) % 2 == 0;
    case 1: return y % 2 == 0;
    case 2: return x % 3 == 0;
    case 3: return (x + y) % 3 == 0;
    case 4: return (x / 3 + y / 2) % 2 == 0;
    case 5: return x * y % 2 + x * y % 3 == 0;
    case 6: return (x * y % 2 + x * y % 3) % 2 == 0;
    default: return ((x + y) % 2 + x * y % 3) % 2 == 0;
    }
}

static void qr_apply_mask(int mask)
{
    int x, y;

    for (y = 0; y < qr_size; y++)
        for (x = 0; x < qr_size; x++)
            if (!qr_fn[y][x] && qr_mask_bit(mask, x, y)) qr_m[y][x] ^= 1;
}

static long qr_penalty(void)
{
    long p = 0;
    int x, y, n = qr_size, dark = 0, k;

    for (y = 0; y < n; y++) {
        int run = 1;

        for (x = 1; x <= n; x++) {
            if (x < n && qr_m[y][x] == qr_m[y][x - 1]) run++;
            else { if (run >= 5) p += 3 + (run - 5); run = 1; }
        }
    }
    for (x = 0; x < n; x++) {
        int run = 1;

        for (y = 1; y <= n; y++) {
            if (y < n && qr_m[y][x] == qr_m[y - 1][x]) run++;
            else { if (run >= 5) p += 3 + (run - 5); run = 1; }
        }
    }
    for (y = 0; y + 1 < n; y++)
        for (x = 0; x + 1 < n; x++)
            if (qr_m[y][x] == qr_m[y][x + 1] && qr_m[y][x] == qr_m[y + 1][x] && qr_m[y][x] == qr_m[y + 1][x + 1]) p += 3;
    for (y = 0; y < n; y++)
        for (x = 0; x < n; x++) {
            int i;

            dark += qr_m[y][x];
            if (x + 10 < n) {           /* 1011101 with four light modules on a side */
                static const int pat[11] = { 1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0 };
                int a = 1, b = 1;

                for (i = 0; i < 11; i++) { if (qr_m[y][x + i] != pat[i]) a = 0; if (qr_m[y][x + i] != pat[10 - i]) b = 0; }
                if (a || b) p += 40;
            }
            if (y + 10 < n) {
                static const int pat[11] = { 1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0 };
                int a = 1, b = 1;

                for (i = 0; i < 11; i++) { if (qr_m[y + i][x] != pat[i]) a = 0; if (qr_m[y + i][x] != pat[10 - i]) b = 0; }
                if (a || b) p += 40;
            }
        }
    k = (int)((labs((long)dark * 20 - (long)n * n * 10) + (long)n * n - 1) / ((long)n * n)) - 1;
    if (k > 0) p += 10L * k;
    return p;
}

/* Encodes len bytes of text. Returns the size in modules (21..41), or 0 if it does not fit (or len is 0). */
static int qr_encode(const char *text, int len)
{
    uint8_t data[200], all[240], eccs[8][40];
    int ecl = 0, v, i, j, dcw, nb, epb, bitpos = 0, best = 0, out = 0, col;
    int starts[8], lens[8], total, short_blocks, short_len, s = 0;
    long bestp = -1;

    if (len <= 0 || len > 150) return 0;
    for (v = 1; v <= QR_MAXV; v++)                       /* the smallest version that holds it at level L... */
        if (12 + 8 * len <= (qr_raw_bytes(v) - qr_ecc_per_block[0][v] * qr_blocks[0][v]) * 8) break;
    if (v > QR_MAXV) return 0;
    if (12 + 8 * len <= (qr_raw_bytes(v) - qr_ecc_per_block[1][v] * qr_blocks[1][v]) * 8) ecl = 1;   /* ...and level M if it fits there too */
    dcw = qr_raw_bytes(v) - qr_ecc_per_block[ecl][v] * qr_blocks[ecl][v];
    nb = qr_blocks[ecl][v];
    epb = qr_ecc_per_block[ecl][v];
    qr_size = 17 + 4 * v;

    /* the bit stream: mode (byte), length, the bytes, terminator, padding */
    memset(data, 0, sizeof data);
#define PUTBITS(val, n) do { int _k; for (_k = (n) - 1; _k >= 0; _k--) { if (((val) >> _k) & 1) data[bitpos >> 3] |= (uint8_t)(0x80 >> (bitpos & 7)); bitpos++; } } while (0)
    PUTBITS(4, 4);
    PUTBITS(len, 8);
    for (i = 0; i < len; i++) PUTBITS((unsigned char)text[i], 8);
#undef PUTBITS
    bitpos += (dcw * 8 - bitpos) < 4 ? (dcw * 8 - bitpos) : 4;
    bitpos = (bitpos + 7) & ~7;
    for (i = bitpos / 8, j = 0; i < dcw; i++, j++) data[i] = (uint8_t)(j % 2 == 0 ? 0xEC : 0x11);

    /* split into blocks, add error correction to each, interleave */
    total = qr_raw_bytes(v);
    short_blocks = nb - total % nb;
    short_len = total / nb - epb;
    for (i = 0; i < nb; i++) {
        lens[i] = short_len + (i < short_blocks ? 0 : 1);
        starts[i] = s;
        s += lens[i];
        qr_rs(data + starts[i], lens[i], epb, eccs[i]);
    }
    for (col = 0; col < short_len + 1; col++)
        for (i = 0; i < nb; i++)
            if (col < lens[i]) all[out++] = data[starts[i] + col];
    for (col = 0; col < epb; col++)
        for (i = 0; i < nb; i++) all[out++] = eccs[i][col];

    /* the fixed patterns */
    memset(qr_m, 0, sizeof qr_m);
    memset(qr_fn, 0, sizeof qr_fn);
    for (i = 0; i < qr_size; i++) { qr_set(6, i, i % 2 == 0); qr_set(i, 6, i % 2 == 0); }
    qr_finder(3, 3); qr_finder(qr_size - 4, 3); qr_finder(3, qr_size - 4);
    if (v >= 2) qr_alignment(qr_align[v], qr_align[v]);
    qr_format(ecl, 0);                    /* reserves the format areas; rewritten below with the chosen mask */

    /* the data bits, in the zigzag order */
    {
        int right, vert, k = 0, total_bits = total * 8;

        for (right = qr_size - 1; right >= 1; right -= 2) {
            if (right == 6) right = 5;
            for (vert = 0; vert < qr_size; vert++)
                for (j = 0; j < 2; j++) {
                    int upward = ((right + 1) & 2) == 0, x = right - j, y = upward ? qr_size - 1 - vert : vert;

                    if (!qr_fn[y][x] && k < total_bits) { qr_m[y][x] = (uint8_t)((all[k >> 3] >> (7 - (k & 7))) & 1); k++; }
                }
        }
    }

    /* the best of the eight masks */
    for (i = 0; i < 8; i++) {
        long p;

        qr_apply_mask(i);
        qr_format(ecl, i);
        p = qr_penalty();
        if (bestp < 0 || p < bestp) { bestp = p; best = i; }
        qr_apply_mask(i);                 /* undo */
    }
    qr_apply_mask(best);
    qr_format(ecl, best);
    return qr_size;
}