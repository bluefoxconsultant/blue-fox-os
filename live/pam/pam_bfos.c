/*
 * pam_bfos.so - connexion par QR de la cle USB live Blue Fox OS (#26870).
 *
 * Une cale volontairement mince : tout le metier (flux d'appareil Authentik,
 * verification de l'identite, creation de l'usager) vit dans une aide Python
 * lancee en root, deja eprouvee a l'installation. Ce module ne fait que :
 *
 *   - lancer l'aide avec un environnement propre et sans heriter de descripteurs ;
 *   - relayer ses messages vers la conversation PAM, et les reponses vers elle ;
 *   - poser PAM_USER sur le nom qu'elle rend (greetd le relit APRES
 *     l'authentification, ce qui permet de creer l'usager a la volee) ;
 *   - ne rendre PAM_SUCCESS que sur un OK explicite ET une sortie 0.
 *
 * Protocole (une ligne par message, "\\" -> "\\\\", saut de ligne -> "\\n") :
 *   INFO <t> | ERROR <t> | ASK <t> | SECRET <t> | USER <nom> | OK | FAIL <raison>
 *
 * Arguments : helper=/chemin/absolu (defaut BFOS_HELPER_DEFAUT).
 * L'aide doit appartenir a root et n'etre inscriptible ni par le groupe ni par
 * les autres : sinon le module refuse, plutot que d'executer en root un
 * fichier que quelqu'un d'autre peut reecrire.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pwd.h>
#include <time.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <syslog.h>
#include <unistd.h>

#define PAM_SM_AUTH
#define PAM_SM_SESSION
#include <security/pam_appl.h>
#include <security/pam_ext.h>
#include <security/pam_modules.h>

#define BFOS_HELPER_DEFAUT "/usr/libexec/bfos-live/pam-helper"
#define LIGNE_MAX 65536
#define NOM_MAX 32

struct aide {
    pid_t pid;
    FILE *lire;   /* sortie de l'aide */
    int ecrire;   /* entree de l'aide */
};

static const char *chemin_aide(int argc, const char **argv)
{
    for (int i = 0; i < argc; i++)
        if (strncmp(argv[i], "helper=", 7) == 0)
            return argv[i] + 7;
    return BFOS_HELPER_DEFAUT;
}

static int aide_sure(pam_handle_t *pamh, const char *chemin)
{
    struct stat st;
    if (chemin[0] != '/') {
        pam_syslog(pamh, LOG_ERR, "aide %s : chemin non absolu", chemin);
        return 0;
    }
    if (stat(chemin, &st) != 0) {
        pam_syslog(pamh, LOG_ERR, "aide %s introuvable : %m", chemin);
        return 0;
    }
    if (!S_ISREG(st.st_mode) || st.st_uid != 0 || (st.st_mode & (S_IWGRP | S_IWOTH))) {
        pam_syslog(pamh, LOG_ERR, "aide %s refusee : doit appartenir a root, sans ecriture groupe/autres", chemin);
        return 0;
    }
    return 1;
}

static void desechapper(char *s)
{
    char *r = s, *w = s;
    while (*r) {
        if (*r == '\\' && r[1]) {
            *w++ = (r[1] == 'n') ? '\n' : r[1];
            r += 2;
        } else {
            *w++ = *r++;
        }
    }
    *w = '\0';
}

/* Ecrit `s` echappe + "\n". SIGPIPE bloque le temps de l'ecriture : une aide
 * morte ne doit pas tuer le processus qui nous charge (greetd, kscreenlocker). */
static int ecrire_reponse(int fd, const char *s)
{
    size_t n = strlen(s);
    char *buf = malloc(2 * n + 2);
    if (!buf)
        return -1;
    size_t j = 0;
    for (size_t i = 0; i < n; i++) {
        if (s[i] == '\\') { buf[j++] = '\\'; buf[j++] = '\\'; }
        else if (s[i] == '\n') { buf[j++] = '\\'; buf[j++] = 'n'; }
        else buf[j++] = s[i];
    }
    buf[j++] = '\n';

    sigset_t pipe_set, ancien;
    sigemptyset(&pipe_set);
    sigaddset(&pipe_set, SIGPIPE);
    pthread_sigmask(SIG_BLOCK, &pipe_set, &ancien);
    int rc = 0;
    size_t fait = 0;
    while (fait < j) {
        ssize_t w = write(fd, buf + fait, j - fait);
        if (w < 0) {
            if (errno == EINTR)
                continue;
            rc = -1;
            break;
        }
        fait += (size_t)w;
    }
    if (rc != 0 && errno == EPIPE) {
        struct timespec zero = {0, 0};
        sigtimedwait(&pipe_set, NULL, &zero); /* vider le SIGPIPE en attente */
    }
    pthread_sigmask(SIG_SETMASK, &ancien, NULL);
    explicit_bzero(buf, 2 * n + 2);
    free(buf);
    return rc;
}

static int lancer(pam_handle_t *pamh, const char *chemin, const char *mode, struct aide *a)
{
    int vers_aide[2], depuis_aide[2];
    if (pipe2(vers_aide, O_CLOEXEC) != 0)
        return -1;
    if (pipe2(depuis_aide, O_CLOEXEC) != 0) {
        close(vers_aide[0]);
        close(vers_aide[1]);
        return -1;
    }

    /* Environnement propre : rien de l'appelant, sauf ce que PAM sait. */
    const void *item;
    char e_service[256] = "PAM_SERVICE=", e_user[256] = "PAM_USER=",
         e_tty[256] = "PAM_TTY=", e_rhost[256] = "PAM_RHOST=";
    if (pam_get_item(pamh, PAM_SERVICE, &item) == PAM_SUCCESS && item)
        strncat(e_service, item, sizeof e_service - strlen(e_service) - 1);
    if (pam_get_item(pamh, PAM_USER, &item) == PAM_SUCCESS && item)
        strncat(e_user, item, sizeof e_user - strlen(e_user) - 1);
    if (pam_get_item(pamh, PAM_TTY, &item) == PAM_SUCCESS && item)
        strncat(e_tty, item, sizeof e_tty - strlen(e_tty) - 1);
    if (pam_get_item(pamh, PAM_RHOST, &item) == PAM_SUCCESS && item)
        strncat(e_rhost, item, sizeof e_rhost - strlen(e_rhost) - 1);
    char *envp[] = {"PATH=/usr/sbin:/usr/bin", "LANG=C.UTF-8", e_service, e_user,
                    e_tty, e_rhost, NULL};

    pid_t parent = getpid();
    pid_t pid = fork();
    if (pid < 0) {
        close(vers_aide[0]); close(vers_aide[1]);
        close(depuis_aide[0]); close(depuis_aide[1]);
        return -1;
    }
    if (pid == 0) {
        /* L'aide meurt avec nous : greetd tue le processus de session quand
         * on annule, et une aide orpheline continuerait d'attendre le QR. */
        prctl(PR_SET_PDEATHSIG, SIGTERM);
        if (getppid() != parent)
            _exit(127);
        if (dup2(vers_aide[0], STDIN_FILENO) < 0 || dup2(depuis_aide[1], STDOUT_FILENO) < 0)
            _exit(127);
        int nul = open("/dev/null", O_WRONLY);
        if (nul >= 0) {
            dup2(nul, STDERR_FILENO);
            if (nul > STDERR_FILENO)
                close(nul);
        }
        closefrom(STDERR_FILENO + 1);
        /* Masque ET dispositions a zero : une application qui ignore SIGTERM
         * rendrait sinon PDEATHSIG et kill sans effet sur l'aide. */
        static const int sigs[] = {SIGTERM, SIGINT, SIGHUP, SIGPIPE, SIGCHLD, SIGQUIT};
        for (size_t i = 0; i < sizeof sigs / sizeof sigs[0]; i++)
            signal(sigs[i], SIG_DFL);
        sigset_t vide;
        sigemptyset(&vide);
        sigprocmask(SIG_SETMASK, &vide, NULL);
        char *args[] = {(char *)chemin, (char *)mode, NULL};
        execve(chemin, args, envp);
        _exit(127);
    }
    close(vers_aide[0]);
    close(depuis_aide[1]);
    a->pid = pid;
    a->ecrire = vers_aide[1];
    a->lire = fdopen(depuis_aide[0], "r");
    if (!a->lire) {
        close(depuis_aide[0]);
        close(a->ecrire);
        kill(pid, SIGKILL);
        waitpid(pid, NULL, 0);
        return -1;
    }
    return 0;
}

/* 1 = sortie 0 ; 0 = autre sortie ; -1 = deja recoltee par l'appelant (SIGCHLD).
 * echeance > 0 : au-dela de `echeance` secondes, SIGKILL (fermeture de session :
 * une deconnexion ne se fige jamais faute de reseau). */
static int terminer(struct aide *a, int tuer, int echeance)
{
    if (a->lire) { fclose(a->lire); a->lire = NULL; }
    if (a->ecrire >= 0) { close(a->ecrire); a->ecrire = -1; }
    if (a->pid <= 0)
        return 0;
    if (tuer)
        kill(a->pid, SIGTERM);
    int st = 0;
    pid_t r;
    if (echeance > 0) {
        struct timespec pause = {0, 100 * 1000 * 1000};
        int tours = echeance * 10;
        while ((r = waitpid(a->pid, &st, WNOHANG)) == 0 && tours-- > 0)
            nanosleep(&pause, NULL);
        if (r == 0) {
            kill(a->pid, SIGKILL);
            do {
                r = waitpid(a->pid, &st, 0);
            } while (r < 0 && errno == EINTR);
        }
    } else {
        do {
            r = waitpid(a->pid, &st, 0);
        } while (r < 0 && errno == EINTR);
    }
    a->pid = 0;
    if (r < 0)
        return errno == ECHILD ? -1 : 0;
    return WIFEXITED(st) && WEXITSTATUS(st) == 0;
}

static int nom_valide(const char *s)
{
    size_t n = strlen(s);
    if (n == 0 || n > NOM_MAX)
        return 0;
    if (!((s[0] >= 'a' && s[0] <= 'z') || s[0] == '_'))
        return 0;
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-'))
            return 0;
    }
    return 1;
}

static int converser(pam_handle_t *pamh, int style, const char *texte, char **reponse)
{
    char *r = NULL;
    int rc = pam_prompt(pamh, style, reponse ? &r : NULL, "%s", texte);
    if (reponse)
        *reponse = r;
    else if (r)
        free(r);
    return rc;
}

PAM_EXTERN int pam_sm_authenticate(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)flags;
    const char *chemin = chemin_aide(argc, argv);
    if (!aide_sure(pamh, chemin))
        return PAM_AUTHINFO_UNAVAIL;

    /* Si l'application demande un compte qui EXISTE (ecran de verrouillage),
     * seul ce compte peut sortir : jamais celui de la personne qui a scanne. */
    char demande[NOM_MAX + 1] = "";
    int demande_existe = 0;
    const void *u0 = NULL;
    if (pam_get_item(pamh, PAM_USER, &u0) == PAM_SUCCESS && u0 && strlen(u0) <= NOM_MAX) {
        strcpy(demande, u0);
        demande_existe = getpwnam(demande) != NULL;
    } else if (u0) {
        return PAM_USER_UNKNOWN;
    }

    struct aide a = {0, NULL, -1};
    if (lancer(pamh, chemin, "auth", &a) != 0) {
        pam_syslog(pamh, LOG_ERR, "impossible de lancer %s : %m", chemin);
        return PAM_AUTHINFO_UNAVAIL;
    }

    char *ligne = NULL;
    size_t cap = 0;
    ssize_t n;
    int ok = 0, echec = 0, conv_rompue = 0;
    char nom[NOM_MAX + 1] = "";

    while (!ok && !echec && (n = getline(&ligne, &cap, a.lire)) >= 0) {
        if (n > LIGNE_MAX) {
            pam_syslog(pamh, LOG_ERR, "ligne de l'aide trop longue");
            echec = 1;
            break;
        }
        if (n > 0 && ligne[n - 1] == '\n')
            ligne[--n] = '\0';
        char *arg = strchr(ligne, ' ');
        if (arg)
            *arg++ = '\0';
        else
            arg = ligne + n; /* chaine vide */
        desechapper(arg);

        if (strcmp(ligne, "INFO") == 0 || strcmp(ligne, "ERROR") == 0) {
            int style = ligne[0] == 'I' ? PAM_TEXT_INFO : PAM_ERROR_MSG;
            if (converser(pamh, style, arg, NULL) != PAM_SUCCESS) {
                conv_rompue = 1;
                break;
            }
        } else if (strcmp(ligne, "ASK") == 0 || strcmp(ligne, "SECRET") == 0) {
            int style = ligne[0] == 'A' ? PAM_PROMPT_ECHO_ON : PAM_PROMPT_ECHO_OFF;
            char *rep = NULL;
            if (converser(pamh, style, arg, &rep) != PAM_SUCCESS || !rep) {
                conv_rompue = 1;
                break;
            }
            int w = ecrire_reponse(a.ecrire, rep);
            explicit_bzero(rep, strlen(rep));
            free(rep);
            if (w != 0) {
                echec = 1;
                break;
            }
        } else if (strcmp(ligne, "USER") == 0) {
            if (nom[0] || !nom_valide(arg)) {
                pam_syslog(pamh, LOG_ERR, "nom d'usager refuse de l'aide");
                echec = 1;
                break;
            }
            strcpy(nom, arg);
        } else if (strcmp(ligne, "OK") == 0) {
            ok = 1;
        } else if (strcmp(ligne, "FAIL") == 0) {
            pam_syslog(pamh, LOG_NOTICE, "refus : %s", arg);
            echec = 1;
        } else {
            pam_syslog(pamh, LOG_ERR, "message inconnu de l'aide");
            echec = 1;
        }
    }
    if (ligne) {
        explicit_bzero(ligne, cap);
        free(ligne);
    }

    int sortie = terminer(&a, !ok, 0);
    if (conv_rompue)
        return PAM_CONV_ERR;
    /* -1 : l'application a recolte l'aide elle-meme (gestionnaire SIGCHLD) ;
     * le OK explicite fait alors foi. */
    if (!ok || sortie == 0 || !nom[0])
        return PAM_AUTH_ERR;
    if (demande_existe && strcmp(nom, demande) != 0) {
        pam_syslog(pamh, LOG_ERR, "l'aide rend %s alors que %s etait demande", nom, demande);
        return PAM_AUTH_ERR;
    }
    /* Defense en profondeur : jamais un compte systeme. */
    struct passwd *pw = getpwnam(nom);
    if (!pw || pw->pw_uid < 1000) {
        pam_syslog(pamh, LOG_ERR, "compte %s absent ou systeme", nom);
        return PAM_AUTH_ERR;
    }
    if (pam_set_item(pamh, PAM_USER, nom) != PAM_SUCCESS)
        return PAM_AUTH_ERR;
    return PAM_SUCCESS;
}

PAM_EXTERN int pam_sm_setcred(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)pamh; (void)flags; (void)argc; (void)argv;
    return PAM_SUCCESS;
}

PAM_EXTERN int pam_sm_open_session(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)pamh; (void)flags; (void)argc; (void)argv;
    return PAM_IGNORE;
}

/* Fermeture : l'aide revoque les jetons et les efface. 15 s au plus. */
PAM_EXTERN int pam_sm_close_session(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)flags;
    const char *chemin = chemin_aide(argc, argv);
    if (!aide_sure(pamh, chemin))
        return PAM_SUCCESS;
    struct aide a = {0, NULL, -1};
    if (lancer(pamh, chemin, "close", &a) != 0)
        return PAM_SUCCESS;
    terminer(&a, 0, 15);
    return PAM_SUCCESS;
}
