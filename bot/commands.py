import bafser_tgapi as tgapi

from bot.bot import Bot


@Bot.add_command(desc="Начало работы")
def start(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    user = bot.user
    return (
        f"👥 {user.get_name()}, это бот очередей\n"
        "Добавьте меня в группу и назначьте администратором. Администратор группы "
        "может создать очередь: /queue_new Название.\n"
        "Участники встают и выходят кнопками под очередью. "
        "В личном чате можно создать очередь для себя.\n"
        "Крестики-нолики в группе: /tic_tac_toe или /tic_tac_toe @username.\n"
        "Все команды: /help"
    )


@Bot.add_command(desc="Справка по командам")
def help(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    def format_command(command):
        name, description = command
        if isinstance(description, str):
            return f"/{name} — {description}"
        text, hints = description
        hints = [hints] if isinstance(hints, str) else hints
        return "\n".join(f"/{name} {hint}" for hint in hints) + f"\n — {text}"

    return (
        "👥 Для всех:\n" + "\n".join(map(format_command, bot.get_my_commands()))
        + "\n\n👨‍🔧 Для администраторов группы (и владельца личного чата):\n"
        + "\n".join(map(format_command, bot.get_my_commands(True)))
        + "\n\nКоманды настройки отправляйте ответом на сообщение очереди. "
        "Блоки и приоритеты перечисляйте с новой строки. "
        "Суффикс \\s убирает сообщение команды и обычное подтверждение."
    )
