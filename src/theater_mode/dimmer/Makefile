# The fortify redefinition belongs in CFLAGS: $(CPPFLAGS) expands first, so a level set
# there would arrive ahead of a caller's CFLAGS and lose to it. The -U must precede the
# -D, or the redefinition warns. -ffile-prefix-map does nothing until a caller adds -g.
CFLAGS ?= -Wall -Wextra -pedantic
override CFLAGS += -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3 -O2 -fstack-protector-strong \
                   -fPIE -ffile-prefix-map=$(CURDIR)=.
override LDFLAGS += -Wl,-z,relro,-z,now -pie

# Prevent the C23 strtol redirect from raising the glibc symbol floor on newer compilers.
# Paired with _DEFAULT_SOURCE in dimmer.c: both are required, neither is sufficient alone.
STD = -std=gnu17

# Ask pkg-config rather than hardcoding paths, so a libwayland installed under a
# non-standard prefix builds too. install.sh checks for both before compiling.
WAYLAND_CFLAGS := $(shell pkg-config --cflags wayland-client)
WAYLAND_LIBS := $(shell pkg-config --libs wayland-client)

# Keep libraries after objects and independent of distro-supplied LDFLAGS.
LDLIBS ?= $(WAYLAND_LIBS) -lm

HERE := $(dir $(lastword $(MAKEFILE_LIST)))
VERSION_FILE := $(HERE)../__init__.py
VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' $(VERSION_FILE))
ifeq ($(strip $(VERSION)),)
VERSION := unknown
endif

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

# Rebuild when a vendored protocol header changes.
HDRS = wlr-layer-shell-unstable-v1-client-protocol.h \
       viewporter-client-protocol.h \
       single-pixel-buffer-v1-client-protocol.h \
       alpha-modifier-v1-client-protocol.h

TARGET = theater-dimmer

all: $(TARGET)

$(TARGET): $(SRCS) $(HDRS) $(VERSION_FILE)
	$(CC) $(STD) $(CPPFLAGS) $(CFLAGS) $(WAYLAND_CFLAGS) -DTHEATER_DIMMER_VERSION='"$(VERSION)"' -I. $(SRCS) $(LDFLAGS) $(LDLIBS) -o $(TARGET)

clean:
	rm -f $(TARGET)

.PHONY: all clean
