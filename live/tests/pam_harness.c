/*
 * Banc d'essai de pam_bfos.so : joue le role d'une application PAM (greetd,
 * login) avec un dossier de configuration a part (pam_start_confdir), sans
 * toucher a /etc/pam.d.
 *
 *   pam_harness <confdir> <service> <usager> auth|close
 *
 * Chaque message de la conversation sort sur stdout, "STYLE\t<texte echappe>".
 * Pour une question, une ligne est lue sur stdin ; "CANCEL" fait echouer la
 * conversation, comme une annulation a l'ecran de connexion.
 * Fin : "RESULT\t<code PAM>\t<PAM_USER>".
 */
#define _GNU_SOURCE
#include <security/pam_appl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void sortir(const char *style, const char *t)
{
    printf("%s\t", style);
    for (; t && *t; t++) {
        if (*t == '\\') fputs("\\\\", stdout);
        else if (*t == '\n') fputs("\\n", stdout);
        else putchar(*t);
    }
    putchar('\n');
    fflush(stdout);
}

static int conv(int n, const struct pam_message **msg, struct pam_response **resp, void *data)
{
    (void)data;
    struct pam_response *r = calloc((size_t)n, sizeof *r);
    if (!r)
        return PAM_BUF_ERR;
    for (int i = 0; i < n; i++) {
        const char *style = "?";
        int question = 0;
        switch (msg[i]->msg_style) {
        case PAM_TEXT_INFO: style = "INFO"; break;
        case PAM_ERROR_MSG: style = "ERROR"; break;
        case PAM_PROMPT_ECHO_ON: style = "ASK"; question = 1; break;
        case PAM_PROMPT_ECHO_OFF: style = "SECRET"; question = 1; break;
        }
        sortir(style, msg[i]->msg);
        if (question) {
            char *ligne = NULL;
            size_t cap = 0;
            ssize_t k = getline(&ligne, &cap, stdin);
            if (k < 0 || strncmp(ligne, "CANCEL", 6) == 0) {
                free(ligne);
                for (int j = 0; j < i; j++) free(r[j].resp);
                free(r);
                return PAM_CONV_ERR;
            }
            if (k > 0 && ligne[k - 1] == '\n') ligne[k - 1] = '\0';
            r[i].resp = ligne;
        }
    }
    *resp = r;
    return PAM_SUCCESS;
}

int main(int argc, char **argv)
{
    if (argc != 5) {
        fprintf(stderr, "usage: %s confdir service usager auth|close\n", argv[0]);
        return 2;
    }
    struct pam_conv c = {conv, NULL};
    pam_handle_t *pamh = NULL;
    int rc = pam_start_confdir(argv[2], argv[3], &c, argv[1], &pamh);
    if (rc != PAM_SUCCESS) {
        printf("RESULT\tstart:%d\t\n", rc);
        return 1;
    }
    if (strcmp(argv[4], "auth") == 0)
        rc = pam_authenticate(pamh, 0);
    else
        rc = pam_close_session(pamh, 0);
    const void *u = NULL;
    pam_get_item(pamh, PAM_USER, &u);
    printf("RESULT\t%d\t%s\n", rc, u ? (const char *)u : "");
    fflush(stdout);
    pam_end(pamh, rc);
    return 0;
}
