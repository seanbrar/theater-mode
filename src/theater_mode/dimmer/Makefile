CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -pedantic
LDFLAGS ?= -lwayland-client -lm

# xdg-shell-protocol.c is linked but never included: the layer-shell bindings name
# xdg_popup_interface in their type table for zwlr_layer_surface_v1::get_popup, so the
# symbol has to exist even though this client never creates a popup. Its client header
# is genuinely unused and is not vendored.
SRCS = dimmer.c \
       wlr-layer-shell-unstable-v1-protocol.c \
       viewporter-protocol.c \
       single-pixel-buffer-v1-protocol.c \
       alpha-modifier-v1-protocol.c \
       xdg-shell-protocol.c

TARGET = theater-dimmer

all: $(TARGET)

$(TARGET): $(SRCS)
	$(CC) $(CFLAGS) -I. $(SRCS) $(LDFLAGS) -o $(TARGET)

clean:
	rm -f $(TARGET)

.PHONY: all clean
