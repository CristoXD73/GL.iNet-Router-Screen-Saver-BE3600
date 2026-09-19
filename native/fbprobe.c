/*
 * fbprobe -- asks a Linux framebuffer driver what it supports, to find out whether
 * tear-free updates (page flipping / vsync) are possible on the BE3600's display.
 *
 *   fbprobe [/dev/fb0]            read-only questions (safe while anything is running)
 *   fbprobe --try-flip [/dev/fb0] also tries to double the virtual height and pan,
 *                                 then restores the original mode. Stop the screen
 *                                 owners first (be3600-anim off) so nothing is drawing.
 *
 * Results and what they mean: docs/TEARING.md.
 * Build:  cc -O2 -o fbprobe native/fbprobe.c   (or cross-compile like build.sh)
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#ifndef FBIO_WAITFORVSYNC
#define FBIO_WAITFORVSYNC _IOW('F', 0x20, unsigned int)
#endif

static void show_var(const char *label, const struct fb_var_screeninfo *v)
{
    printf("%s: visible %ux%u, virtual %ux%u, offset (%u,%u), %u bpp, vmode 0x%x, pixclock %u\n",
           label, v->xres, v->yres, v->xres_virtual, v->yres_virtual, v->xoffset, v->yoffset,
           v->bits_per_pixel, v->vmode, v->pixclock);
}

int main(int argc, char **argv)
{
    int try_flip = 0, fd;
    const char *dev = "/dev/fb0";
    struct fb_fix_screeninfo fix;
    struct fb_var_screeninfo var, orig;
    unsigned int arg = 0;
    void *map;
    int i;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--try-flip") == 0) try_flip = 1;
        else dev = argv[i];
    }

    fd = open(dev, O_RDWR);
    if (fd < 0) { perror(dev); return 1; }

    if (ioctl(fd, FBIOGET_FSCREENINFO, &fix) != 0) { perror("FBIOGET_FSCREENINFO"); return 1; }
    if (ioctl(fd, FBIOGET_VSCREENINFO, &var) != 0) { perror("FBIOGET_VSCREENINFO"); return 1; }
    orig = var;

    printf("driver id        : %.16s\n", fix.id);
    printf("framebuffer size : %u bytes (smem_len), line length %u\n", fix.smem_len, fix.line_length);
    printf("one frame        : %u bytes\n", var.yres * fix.line_length);
    printf("spare memory     : %d bytes (room for %d extra page(s))\n",
           (int)fix.smem_len - (int)(var.yres * fix.line_length),
           (int)(fix.smem_len / (var.yres * fix.line_length)) - 1);
    printf("panning steps    : xpan %u, ypan %u, ywrap %u (0 = panning not supported)\n",
           fix.xpanstep, fix.ypanstep, fix.ywrapstep);
    printf("accel            : %u, type %u, visual %u\n", fix.accel, fix.type, fix.visual);
    show_var("current mode   ", &var);

    map = mmap(NULL, fix.smem_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    printf("mmap             : %s\n", map == MAP_FAILED ? strerror(errno) : "works");
    if (map != MAP_FAILED) munmap(map, fix.smem_len);

    if (ioctl(fd, FBIO_WAITFORVSYNC, &arg) == 0) printf("FBIO_WAITFORVSYNC: supported\n");
    else printf("FBIO_WAITFORVSYNC: not supported (%s)\n", strerror(errno));

    var.yoffset = 0;
    if (ioctl(fd, FBIOPAN_DISPLAY, &var) == 0) printf("FBIOPAN_DISPLAY  : accepted (offset 0)\n");
    else printf("FBIOPAN_DISPLAY  : rejected (%s)\n", strerror(errno));

    if (try_flip) {
        var = orig;
        var.yres_virtual = orig.yres * 2;
        if (ioctl(fd, FBIOPUT_VSCREENINFO, &var) == 0) {
            struct fb_var_screeninfo now;
            ioctl(fd, FBIOGET_VSCREENINFO, &now);
            show_var("after doubling ", &now);
            if (now.yres_virtual >= orig.yres * 2) {
                now.yoffset = orig.yres;
                printf("pan to second page: %s\n",
                       ioctl(fd, FBIOPAN_DISPLAY, &now) == 0 ? "accepted" : strerror(errno));
                now.yoffset = 0;
                ioctl(fd, FBIOPAN_DISPLAY, &now);
            } else {
                printf("the driver accepted the call but did not grow the virtual height\n");
            }
        } else {
            printf("doubling the virtual height: rejected (%s)\n", strerror(errno));
        }
        if (ioctl(fd, FBIOPUT_VSCREENINFO, &orig) == 0) printf("original mode restored\n");
        else printf("WARNING: could not restore the original mode (%s)\n", strerror(errno));
        ioctl(fd, FBIOGET_VSCREENINFO, &var);
        show_var("final mode     ", &var);
    }

    close(fd);
    return 0;
}
