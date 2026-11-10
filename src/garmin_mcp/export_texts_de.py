"""The German texts of ``garmin-mcp-auth --export --lang de``.

Fork-only: this module is the only place with German texts, so a proposal for upstream, which is English,
leaves it out (the export code in ``auth_cli`` keeps English defaults and loads this module only for
``--lang de``). The keys are those of ``auth_cli.EXPORT_TEXTS_EN``.
"""

TEXTS = {
    "banner": "Garmin MCP Anmeldung für fatgu.ai",
    "heading": "Garmin-Zugang",
    "email_prompt": "Garmin-E-Mail: ",
    "password_prompt": "Garmin-Passwort (wird nicht angezeigt): ",
    "logging_in": "Ich melde mich bei Garmin an...",
    "mfa_hint": (
        "Garmin hat dir gerade einen Code per E-Mail geschickt. Schau in dein Postfach, auch im "
        "Spam-Ordner (oder in deine Authenticator-App, wenn du eine eingerichtet hast). "
        "Der Code gilt nur wenige Minuten."
    ),
    "mfa_prompt": "Code von Garmin: ",
    "folder_missing": "Der Ordner '{parent}' existiert nicht.",
    "is_link": "'{path}' ist eine Verknüpfung; dorthin schreibe ich nicht.",
    "not_regular": "'{path}' existiert und ist keine normale Datei.",
    "exists": "'{path}' gibt es schon. Mit --force wird sie ersetzt.",
    "not_regular_replace": "'{path}' ist keine normale Datei; ich ersetze sie nicht.",
    "no_profile": "Garmin hat kein Profil geliefert; die Anmeldung ist nicht brauchbar.",
    "no_tokens": "Garmin hat nicht die erwarteten Zugangsdaten ausgestellt.",
    "creds_missing": "E-Mail oder Passwort fehlen (oder sind doppelt angegeben).",
    "login_failed": "Anmeldung fehlgeschlagen. Prüfe E-Mail, Passwort und den Code.",
    "too_many": "Zu viele Anfragen. Warte ein paar Minuten und versuch es noch einmal.",
    "connection": "Garmin war nicht erreichbar oder hat unerwartet geantwortet.",
    "write_failed": "Die Datei '{path}' konnte nicht geschrieben werden.",
    "unexpected": "Unerwarteter Fehler ({name}).",
    "done": "Fertig. Die Datei liegt hier: {path}. Lade sie auf fatgu.ai hoch und lösch sie danach.",
}
