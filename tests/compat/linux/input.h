/* Test builds on macOS only: the few parts of Linux's <linux/input.h> that
 * native/be3600-player.c uses, so the player can be built and tested on a Mac
 * against fake framebuffers and touch devices (tests/run.sh). The router build
 * always uses the real header. Values and layouts are Linux's own; on a 64-bit
 * Mac, struct input_event is 24 bytes, the same as on the aarch64 router. */
#ifndef BE3600_COMPAT_LINUX_INPUT_H
#define BE3600_COMPAT_LINUX_INPUT_H

#include <stdint.h>
#include <sys/ioccom.h>
#include <sys/time.h>

struct input_event {
    struct timeval time;
    uint16_t type;
    uint16_t code;
    int32_t value;
};

struct input_absinfo {
    int32_t value, minimum, maximum, fuzz, flat, resolution;
};

#define EV_SYN 0x00
#define EV_KEY 0x01
#define EV_ABS 0x03
#define SYN_REPORT 0

#define BTN_TOOL_FINGER 0x145
#define BTN_TOUCH 0x14a

#define ABS_X 0x00
#define ABS_Y 0x01
#define ABS_MT_POSITION_X 0x35
#define ABS_MT_POSITION_Y 0x36
#define ABS_MT_TRACKING_ID 0x39

#define EVIOCGABS(abs) _IOR('E', 0x40 + (abs), struct input_absinfo)
#define EVIOCSCLOCKID _IOW('E', 0xa0, int)

#endif
