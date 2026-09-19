#!/usr/bin/lua

-- be3600-player.lua -- plays a .bea animation on the BE3600's front display
-- by writing raw frames straight to /dev/fb0, looping forever.
-- Usage: be3600-player.lua [file.bea]   (default /etc/be3600-screen/active.bea)
--
-- The display is 76 x 284 pixels at 16 bits per pixel with a 152-byte
-- row stride, so one frame is 152 * 284 = 43168 bytes.
-- File format: see docs/BEA-FORMAT.md.

local PACK =
    arg[1] or
    "/etc/be3600-screen/active.bea"

local FRAME_BYTES = 43168

-- Test hooks (unused in normal operation):
--   BE3600_FB     framebuffer path; point it at an ordinary file to test on a PC
--   BE3600_LOOPS  exit after this many complete passes instead of looping forever
local FB_PATH =
    os.getenv("BE3600_FB") or
    "/dev/fb0"

local MAX_LOOPS =
    tonumber(os.getenv("BE3600_LOOPS") or "")

local loops = 0


local function u16(s,p)

    local a,b =
        s:byte(p,p+1)

    assert(a and b)

    return a + b*256
end


local function u32(s,p)

    local a,b,c,d =
        s:byte(p,p+3)

    assert(a and d)

    return
        a +
        b*256 +
        c*65536 +
        d*16777216
end


local f =
    assert(
        io.open(
            PACK,
            "rb"
        )
    )


local header =
    assert(
        f:read(12)
    )


local MAGIC =
    header:sub(1,4)

assert(
    MAGIC == "BEA1" or MAGIC == "BEA2",
    "Invalid BEA animation"
)


local fps =
    u16(header,5)

local records =
    u16(header,7)

local bytes =
    u32(header,9)


assert(
    bytes == FRAME_BYTES,
    "Wrong framebuffer size"
)


assert(
    fps >= 1 and fps <= 24,
    "Invalid FPS"
)


-- microseconds per tick
local delay =
    math.floor(
        1000000 / fps
    )


-- BEA2 (changes only): the framebuffer keeps its picture between records, so
-- the file is opened once and only the changed byte ranges are rewritten.
-- Record: u16 run, u8 kind (0 full, 1 delta, 2 hold), u32 payload length, payload.
-- The file has already been validated by be3600-bea-check.lua.
local function play_bea2()

    local fb =
        assert(
            io.open(
                FB_PATH,
                "r+b"
            )
        )

    while true do

        f:seek(
            "set",
            12
        )

        for i=1,records do

            local rec =
                assert(
                    f:read(7)
                )

            local run =
                u16(
                    rec,
                    1
                )

            local kind =
                rec:byte(3)

            local plen =
                u32(
                    rec,
                    4
                )

            local payload =
                ""

            if plen > 0 then
                payload =
                    assert(
                        f:read(plen)
                    )
            end


            if kind == 0 then

                fb:seek("set", 0)
                assert(fb:write(payload))

            elseif kind == 1 then

                local n =
                    u16(
                        payload,
                        1
                    )

                local p = 3

                for s=1,n do

                    local off =
                        u32(
                            payload,
                            p
                        )

                    local len =
                        u16(
                            payload,
                            p + 4
                        )

                    fb:seek("set", off)
                    assert(
                        fb:write(
                            payload:sub(
                                p + 6,
                                p + 5 + len
                            )
                        )
                    )

                    p = p + 6 + len
                end
            end

            fb:flush()

            os.execute(
                "/bin/usleep " ..
                tostring(
                    delay * run
                )
            )
        end

        loops = loops + 1

        if MAX_LOOPS and loops >= MAX_LOOPS then
            os.exit(0)
        end
    end
end


if MAGIC == "BEA2" then
    play_bea2()
end


while true do

    f:seek(
        "set",
        12
    )


    for i=1,records do

        -- how many ticks this frame stays on screen
        local rawRun =
            assert(
                f:read(2)
            )

        local run =
            u16(
                rawRun,
                1
            )


        local frame =
            assert(
                f:read(bytes)
            )


        --
        -- Open/write/close gives us a guaranteed
        -- framebuffer offset of zero every frame.
        --

        local fb =
            assert(
                io.open(
                    FB_PATH,
                    "wb"
                )
            )


        assert(
            fb:write(frame)
        )

        fb:flush()
        fb:close()


        os.execute(
            "/bin/usleep " ..
            tostring(
                delay * run
            )
        )
    end

    loops = loops + 1

    if MAX_LOOPS and loops >= MAX_LOOPS then
        os.exit(0)
    end
end
