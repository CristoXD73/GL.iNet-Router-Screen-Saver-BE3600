# Shared by the be3600 screen saver scripts. Sourced, never run.
#
# The config is plain shell (KEY="value"), but these read it with sed rather than sourcing it,
# so a stray line in a file someone edited by hand cannot run as a command.

: "${CONF:=${BE3600_CONF:-/etc/be3600-screen/config}}"

# conf_get KEY -- the value, unquoted, without any trailing comment or spaces. Empty if unset.
conf_get() {
    sed -n "s/^$1=[\"']\{0,1\}\([^\"'#]*\).*/\1/p" "$CONF" 2>/dev/null | head -n 1 | sed 's/[[:space:]]*$//'
}

# conf_set KEY VALUE -- replace the line, or add it. The value is written in double quotes.
conf_set() {
    if grep -q "^$1=" "$CONF" 2>/dev/null; then
        sed "s|^$1=.*|$1=\"$2\"|" "$CONF" > "$CONF.new" && mv "$CONF.new" "$CONF"
    else
        echo "$1=\"$2\"" >> "$CONF"
    fi
}
