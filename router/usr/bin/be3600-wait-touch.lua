#!/usr/bin/lua

-- be3600-wait-touch.lua -- watches the touchscreen.
--
--   be3600-wait-touch.lua                  wait for a touch, then exit 0
--   be3600-wait-touch.lua --wait-release   wait for the finger to lift, then exit 0
--   be3600-wait-touch.lua --which          print the touch device it would use, then exit
--
-- Exit codes: 0 = the thing being waited for happened, 2 = the touch device
-- could not be read.
--
-- Which device: TOUCH_DEVICE (from /etc/be3600-screen/config) if set; otherwise
-- the first /sys/class/input/eventN whose name contains "touch" (on the BE3600
-- that is "Hynitron CST816X Touchscreen"); otherwise /dev/input/event0.
--
-- The touch controller is a Hynitron CST816X. It reports ABS_X / ABS_Y
-- coordinates, ABS_MT_TRACKING_ID (0 while a finger is down, -1 when it lifts)
-- and SYN_REPORT. It does not send BTN_TOUCH, but that and BTN_TOOL_FINGER are
-- accepted too in case a firmware update changes that.

local function detect_device()

    local override = os.getenv("TOUCH_DEVICE")

    if override and override ~= "" then
        return override, "TOUCH_DEVICE"
    end

    -- BE3600_SYS_INPUT is a test hook (a fake /sys/class/input tree); it works only when
    -- BE3600_TESTING is set.
    local sys = (os.getenv("BE3600_TESTING") and os.getenv("BE3600_SYS_INPUT")) or "/sys/class/input"

    for n = 0, 31 do
        local f = io.open(string.format("%s/event%d/device/name", sys, n), "r")

        if f then
            local name = f:read("*l") or ""
            f:close()

            if name:lower():find("touch", 1, true) then
                return string.format("/dev/input/event%d", n), name
            end
        end
    end

    return "/dev/input/event0", "default"
end

local DEVICE, WHY = detect_device()

local MODE = arg and arg[1] or nil

if MODE == "--which" then
    print(DEVICE .. " (" .. WHY .. ")")
    os.exit(0)
end

local WAIT_RELEASE = (MODE == "--wait-release")


local function u16(s,p)
    local a,b = s:byte(p,p+1)

    if not a or not b then
        return nil
    end

    return a + b*256
end


local function i32(s,p)
    local a,b,c,d =
        s:byte(p,p+3)

    if not a or not d then
        return nil
    end

    local n =
        a +
        b*256 +
        c*65536 +
        d*16777216

    if n >= 2147483648 then
        n = n - 4294967296
    end

    return n
end


local f =
    assert(
        io.open(
            DEVICE,
            "rb"
        )
    )

-- IMPORTANT: unbuffered reads.
--
-- Observed on the GL-BE3600: with Lua's default buffered stream,
-- f:read(24) on this device returned nil immediately, with no touch,
-- so this script exited 2 on every call. With buffering off it blocks
-- until a real event arrives and detects touches correctly. (The raw
-- device itself was always fine: `dd` on it blocked and delivered
-- events.)
--
-- The likely cause: input devices reject reads smaller than one whole
-- struct input_event (24 bytes here), and a buffered stdio read can be
-- issued slightly smaller than the 24 bytes asked for. That mechanism
-- was not confirmed; the fix below is what was verified.
f:setvbuf("no")


if WAIT_RELEASE then

    -- Block until the finger lifts. A real tap is not instantaneous: while a
    -- finger stays down, the controller keeps reporting it, so a helper
    -- started right after a press would immediately see that SAME contact
    -- and mistake it for a brand new touch. The supervisor uses this mode to
    -- wait for a genuine lift-off before it starts timing a possible second
    -- tap, so one tap is never miscounted as the start of a double-tap.
    while true do

        local e = f:read(24)

        if not e or #e ~= 24 then
            os.exit(2)
        end

        local typ   = u16(e,17)
        local code  = u16(e,19)
        local value = i32(e,21)

        -- EV_KEY / BTN_TOUCH or BTN_TOOL_FINGER / release
        if typ == 1 and (code == 330 or code == 325) and value == 0 then
            os.exit(0)
        end

        -- EV_ABS / ABS_MT_TRACKING_ID / -1 means the finger lifted
        if typ == 3 and code == 57 and value < 0 then
            os.exit(0)
        end
    end
end


-- Default mode: exit 0 as soon as any finger touches the screen.
while true do

    local e = f:read(24)

    if not e or #e ~= 24 then
        os.exit(2)
    end


    local typ =
        u16(e,17)

    local code =
        u16(e,19)

    local value =
        i32(e,21)


    -- EV_KEY / BTN_TOUCH / press
    if typ == 1 and
       code == 330 and
       value == 1 then

        os.exit(0)
    end


    -- EV_KEY / BTN_TOOL_FINGER / press
    if typ == 1 and
       code == 325 and
       value == 1 then

        os.exit(0)
    end


    -- EV_ABS / ABS_MT_TRACKING_ID
    --
    -- >= 0 begins a finger contact.
    -- -1 means release.
    if typ == 3 and
       code == 57 and
       value >= 0 then

        os.exit(0)
    end
end
