# pam_bfos.so - cale PAM de la cle USB live (#26870).
# Exige pam-devel (Fedora) ; construit dans le conteneur de l'image.
CFLAGS ?= -O2 -g
CFLAGS += -fPIC -Wall -Wextra -Werror -D_FORTIFY_SOURCE=3 -fstack-protector-strong
LDFLAGS += -shared -Wl,-z,relro,-z,now -Wl,--no-undefined
LDLIBS = -lpam

pam_bfos.so: pam_bfos.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS) $(LDLIBS)

pam_harness: ../tests/pam_harness.c
	$(CC) -O0 -g -Wall -Wextra -Werror -o $@ $< -lpam

clean:
	rm -f pam_bfos.so pam_harness

.PHONY: clean
