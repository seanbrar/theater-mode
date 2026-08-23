# The fortify redefinition belongs in CFLAGS: $(CPPFLAGS) expands first, so a level set
# there would arrive ahead of a caller's CFLAGS and lose to it. The -U must precede the
# -D, or the redefinition warns. -ffile-prefix-map does nothing until a caller adds -g.
CFLAGS ?= -Wall -Wextra -pedantic
override CFLAGS += -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3 -O2 -fstack-protector-strong \
                   -fPIE -ffile-prefix-map=$(CURDIR)=.
override LDFLAGS += -Wl,-z,relro,-z,now -pie

# Prevent the C23 strtol redirect from raising the glibc symbol floor on newer
# compilers: building this as C23 pulls in GLIBC_2.38 and breaks the 2.35 release
# floor that bin/check-abi-floor enforces. Unlike the dimmer, no source-side
# feature-test macro is needed alongside it.
STD = -std=gnu17

LDLIBS ?= -lm

HERE := $(dir $(lastword $(MAKEFILE_LIST)))
VERSION_FILE := $(HERE)../__init__.py
VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' $(VERSION_FILE))
ifeq ($(strip $(VERSION)),)
VERSION := unknown
endif

SRCS = main.c pipeline.c
HDRS = pipeline.h stb_image.h

TARGET = theater-art

all: $(TARGET)

$(TARGET): $(SRCS) $(HDRS) $(VERSION_FILE)
	$(CC) $(STD) $(CPPFLAGS) $(CFLAGS) -DTHEATER_ART_VERSION='"$(VERSION)"' -I. $(SRCS) $(LDFLAGS) $(LDLIBS) -o $(TARGET)

clean:
	rm -f $(TARGET)

.PHONY: all clean
