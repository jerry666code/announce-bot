import os
import re

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID = int(os.environ["GUILD_ID"])
ANNOUNCE_ROLE_ID = int(os.environ["ANNOUNCE_ROLE_ID"])

GUILD_OBJECT = discord.Object(id=GUILD_ID)

COLOR_CHOICES = {
    "Синий": discord.Color.blurple(),
    "Зелёный": discord.Color.green(),
    "Красный": discord.Color.red(),
    "Жёлтый": discord.Color.gold(),
    "Фиолетовый": discord.Color.purple(),
}

intents = discord.Intents.default()


def has_role(member: discord.Member, role_id: int) -> bool:
    return any(role.id == role_id for role in member.roles)


class AnnounceModal(discord.ui.Modal, title="Новое объявление"):
    заголовок = discord.ui.TextInput(label="Заголовок", max_length=256)
    текст = discord.ui.TextInput(
        label="Текст",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        placeholder="Часть текста можно скрыть спойлером: ||скрытый текст||",
    )

    def __init__(self, channel, image, color, role):
        super().__init__()
        self.channel = channel
        self.image = image
        self.color = color
        self.role = role

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        embed = discord.Embed(title=str(self.заголовок), description=str(self.текст), color=self.color)

        if self.role is None:
            content = None
            allowed_mentions = discord.AllowedMentions.none()
        elif self.role.is_default():
            content = "||@everyone||"
            allowed_mentions = discord.AllowedMentions(everyone=True)
        else:
            content = f"||{self.role.mention}||"
            allowed_mentions = discord.AllowedMentions(roles=[self.role])

        if self.image is not None:
            is_spoiler = self.image.is_spoiler()
            file = await self.image.to_file(spoiler=is_spoiler)
            if is_spoiler:
                # Картинка внутри эмбеда не может быть размыта — спойлеры
                # работают только у обычных вложений, поэтому шлём отдельно.
                await self.channel.send(content=content, embed=embed, file=file, allowed_mentions=allowed_mentions)
            else:
                embed.set_image(url=f"attachment://{file.filename}")
                await self.channel.send(content=content, embed=embed, file=file, allowed_mentions=allowed_mentions)
        else:
            await self.channel.send(content=content, embed=embed, allowed_mentions=allowed_mentions)

        await interaction.followup.send("Объявление отправлено.", ephemeral=True)


class AnnounceEditModal(discord.ui.Modal, title="Редактировать объявление"):
    заголовок = discord.ui.TextInput(label="Заголовок", max_length=256)
    текст = discord.ui.TextInput(
        label="Текст",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        placeholder="Часть текста можно скрыть спойлером: ||скрытый текст||",
    )

    def __init__(self, message: discord.Message):
        super().__init__()
        self.message = message
        embed = message.embeds[0]
        self.заголовок.default = embed.title or ""
        self.текст.default = embed.description or ""

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Меняем только заголовок/текст — цвет, картинку и упоминание роли
        # эмбед уже несёт в себе, модалка Discord не даёт редактировать вложения.
        embed = self.message.embeds[0]
        embed.title = str(self.заголовок)
        embed.description = str(self.текст)

        try:
            await self.message.edit(embed=embed)
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось отредактировать сообщение: {e}", ephemeral=True)
            return

        await interaction.followup.send("Объявление обновлено.", ephemeral=True)


RULES_EMBED_TITLE = "📋 Правила сервера"
# Лимиты эмбеда Discord: не больше 25 полей и не больше 6000 символов суммарно
# (заголовок + все name/value полей) — оставляем небольшой запас на заголовок.
MAX_RULE_FIELDS = 25
MAX_EMBED_CHARS = 5900


_LEADING_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)")


def _rule_sort_key(номер: str):
    """Сортируем по ведущему числу в номере: "1.2 – Оскорбления" -> (1, 2), а
    не по всей строке целиком — иначе с текстом после номера порядок ломается.
    Номера совсем без ведущего числа уходят в конец списка."""
    m = _LEADING_NUMBER_RE.match(номер.strip())
    if not m:
        return (1, номер)
    parts = tuple(int(p) for p in m.group(1).split("."))
    return (0, parts, номер)


async def find_rules_message(channel: discord.TextChannel, bot_user_id: int):
    """Ищем уже отправленный свод правил в канале, чтобы дописывать пункты в
    него, а не плодить новое сообщение на каждый /rule."""
    async for msg in channel.history(limit=200):
        if msg.author.id == bot_user_id and msg.embeds and msg.embeds[0].title == RULES_EMBED_TITLE:
            return msg
    return None


_RULE_FIELD_VALUE_RE = re.compile(
    r"^(?P<описание>.*)\n\n⚠️ \*\*Наказание:\*\* (?P<наказание>.*?)   •   ⏱️ \*\*Длительность:\*\* (?P<длительность>.*)$",
    re.DOTALL,
)


def _parse_rule_field_value(value: str):
    """Разбирает значение поля обратно на описание/наказание/длительность,
    чтобы предзаполнить ими форму редактирования. Формат жёстко завязан на
    то, как RuleModal его собирает — при желании поменять оформление пункта
    поправьте оба места."""
    m = _RULE_FIELD_VALUE_RE.match(value)
    if not m:
        return None
    return m.group("описание"), m.group("наказание"), m.group("длительность")


class RuleModal(discord.ui.Modal, title="Пункт правил"):
    # 90, не 100 — оставляем запас на "📌 " в имени поля эмбеда и на то, что
    # номер и так же идёт как label/value SelectOption'а в /rule-редакторе,
    # у которых у самих жёсткий лимит Discord в 100 символов.
    номер = discord.ui.TextInput(label="Номер пункта", placeholder="1.2 – Оскорбления", max_length=90)
    описание = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, max_length=900)
    наказание = discord.ui.TextInput(label="Наказание", placeholder="Тайм-аут / Бан", max_length=100)
    длительность = discord.ui.TextInput(label="Длительность", placeholder="1 час / 6ч / 1д", max_length=100)

    def __init__(self, channel, номер: str = None, prefill: tuple = None):
        super().__init__()
        self.channel = channel
        # Задан только когда модалку открыли на редактирование существующего
        # пункта — если в форме поменяют номер, старую запись нужно убрать,
        # а не оставить рядом с новой.
        self.original_номер = номер if prefill is not None else None
        if номер:
            self.номер.default = номер
        if prefill:
            описание, наказание, длительность = prefill
            self.описание.default = описание
            self.наказание.default = наказание
            self.длительность.default = длительность

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        номер = str(self.номер).strip()
        field_name = f"📌 {номер}"
        field_value = (
            f"{self.описание}\n\n"
            f"⚠️ **Наказание:** {self.наказание}   •   ⏱️ **Длительность:** {self.длительность}"
        )
        if len(field_value) > 1024:
            await interaction.followup.send(
                "Слишком длинный пункт — описание вместе с наказанием и длительностью "
                "не помещается в лимит Discord на одно поле (1024 символа). Сократите текст.",
                ephemeral=True,
            )
            return

        try:
            rules_message = await find_rules_message(self.channel, interaction.client.user.id)
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось прочитать историю канала: {e}", ephemeral=True)
            return

        embed = rules_message.embeds[0] if rules_message is not None else discord.Embed(
            title=RULES_EMBED_TITLE, color=discord.Color.red()
        )

        fields = {f.name: f.value for f in embed.fields}
        if self.original_номер and self.original_номер != номер:
            fields.pop(f"📌 {self.original_номер}", None)

        is_update = field_name in fields
        fields[field_name] = field_value

        if not is_update and len(fields) > MAX_RULE_FIELDS:
            await interaction.followup.send(
                f"В своде правил уже {MAX_RULE_FIELDS} пунктов — это лимит одного сообщения Discord. "
                "Удалите/объедините старые пункты вручную в самом сообщении.",
                ephemeral=True,
            )
            return

        total_chars = len(RULES_EMBED_TITLE) + sum(len(name) + len(value) for name, value in fields.items())
        if total_chars > MAX_EMBED_CHARS:
            await interaction.followup.send(
                "Свод правил почти достиг лимита Discord на длину сообщения (6000 символов) — "
                "сократите текст существующих пунктов, чтобы добавить новый.",
                ephemeral=True,
            )
            return

        embed.clear_fields()
        for name in sorted(fields, key=lambda n: _rule_sort_key(n[len("📌 "):])):
            embed.add_field(name=name, value=fields[name], inline=False)

        try:
            if rules_message is not None:
                await rules_message.edit(embed=embed)
            else:
                await self.channel.send(embed=embed)
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось сохранить пункт правил: {e}", ephemeral=True)
            return

        verb = "обновлён" if is_update else "добавлен"
        await interaction.followup.send(f"Пункт {номер} {verb} в своде правил.", ephemeral=True)


class RuleEditSelect(discord.ui.Select):
    def __init__(self, message: discord.Message):
        embed = message.embeds[0]
        # Value/label — сам номер (без "📌 "): у SelectOption лимит Discord в
        # 100 символов и на label, и на value, а "📌 " в имени поля эмбеда его
        # уже не учитывает.
        options = [
            discord.SelectOption(label=f.name[len("📌 "):], value=f.name[len("📌 "):])
            for f in embed.fields
        ]
        super().__init__(placeholder="Какой пункт отредактировать?", options=options)
        self.message = message

    async def callback(self, interaction: discord.Interaction):
        номер = self.values[0]
        field_name = f"📌 {номер}"
        embed = self.message.embeds[0]
        field = next((f for f in embed.fields if f.name == field_name), None)
        if field is None:
            await interaction.response.send_message(
                "Этот пункт уже не найден в своде — возможно, его успели изменить.", ephemeral=True
            )
            return

        prefill = _parse_rule_field_value(field.value)
        if prefill is None:
            await interaction.response.send_message(
                "Не удалось разобрать содержимое пункта на составляющие — отредактируйте его вручную через /rule.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(RuleModal(self.message.channel, номер=номер, prefill=prefill))


class RuleEditView(discord.ui.View):
    def __init__(self, message: discord.Message):
        super().__init__(timeout=180)
        self.add_item(RuleEditSelect(message))


class AnnounceBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, status=discord.Status.invisible)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD_OBJECT)
        await self.tree.sync(guild=GUILD_OBJECT)


bot = AnnounceBot()


@bot.tree.command(name="announce", description="Отправить оформленное объявление", guild=GUILD_OBJECT)
@app_commands.describe(
    канал="Куда отправить (по умолчанию — текущий канал)",
    изображение="Картинка/баннер для объявления (необязательно)",
    цвет="Цвет полоски слева (необязательно, по умолчанию синий)",
    роль="Кого упомянуть перед объявлением — можно выбрать и @everyone (необязательно)",
)
@app_commands.choices(цвет=[app_commands.Choice(name=k, value=k) for k in COLOR_CHOICES])
async def announce(
    interaction: discord.Interaction,
    канал: discord.TextChannel = None,
    изображение: discord.Attachment = None,
    цвет: app_commands.Choice[str] = None,
    роль: discord.Role = None,
):
    if not isinstance(interaction.user, discord.Member) or not has_role(interaction.user, ANNOUNCE_ROLE_ID):
        await interaction.response.send_message("Эта команда доступна только администрации.", ephemeral=True)
        return

    target = канал or interaction.channel
    color = COLOR_CHOICES[цвет.value] if цвет is not None else discord.Color.blurple()
    await interaction.response.send_modal(AnnounceModal(target, изображение, color, роль))


@bot.tree.context_menu(name="Редактировать", guild=GUILD_OBJECT)
async def edit_message(interaction: discord.Interaction, message: discord.Message):
    if not isinstance(interaction.user, discord.Member) or not has_role(interaction.user, ANNOUNCE_ROLE_ID):
        await interaction.response.send_message("Эта команда доступна только администрации.", ephemeral=True)
        return

    if message.author.id != interaction.client.user.id or not message.embeds:
        await interaction.response.send_message("Это не сообщение, отправленное этим ботом.", ephemeral=True)
        return

    if message.embeds[0].title == RULES_EMBED_TITLE:
        if not message.embeds[0].fields:
            await interaction.response.send_message("В своде правил пока нет ни одного пункта.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Выберите пункт для редактирования:", view=RuleEditView(message), ephemeral=True
        )
        return

    await interaction.response.send_modal(AnnounceEditModal(message))


@bot.tree.command(name="rule", description="Добавить/обновить пункт в своде правил канала", guild=GUILD_OBJECT)
@app_commands.describe(канал="В каком канале свод правил (по умолчанию — текущий канал)")
async def rule(interaction: discord.Interaction, канал: discord.TextChannel = None):
    if not isinstance(interaction.user, discord.Member) or not has_role(interaction.user, ANNOUNCE_ROLE_ID):
        await interaction.response.send_message("Эта команда доступна только администрации.", ephemeral=True)
        return

    target = канал or interaction.channel
    await interaction.response.send_modal(RuleModal(target))


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
