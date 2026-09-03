import os

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

    def __init__(self, channel, image, color, mention_everyone):
        super().__init__()
        self.channel = channel
        self.image = image
        self.color = color
        self.mention_everyone = mention_everyone

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        embed = discord.Embed(title=str(self.заголовок), description=str(self.текст), color=self.color)
        content = "||@everyone||" if self.mention_everyone else None
        allowed_mentions = discord.AllowedMentions(everyone=self.mention_everyone)

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


class RuleModal(discord.ui.Modal, title="Новый пункт правил"):
    номер = discord.ui.TextInput(label="Номер пункта", placeholder="1.2", max_length=16)
    описание = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, max_length=1500)
    наказание = discord.ui.TextInput(label="Наказание", placeholder="Тайм-аут / Бан", max_length=100)
    длительность = discord.ui.TextInput(label="Длительность", placeholder="1 час / 6ч / 1д", max_length=100)

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        embed = discord.Embed(title=f"Пункт – {self.номер}", color=discord.Color.red())
        embed.add_field(name="📋 Описание", value=str(self.описание), inline=False)
        embed.add_field(name="⚠️ Наказание", value=str(self.наказание), inline=True)
        embed.add_field(name="⏱️ Длительность", value=str(self.длительность), inline=True)

        await self.channel.send(embed=embed)
        await interaction.followup.send("Пункт правил отправлен.", ephemeral=True)


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
    упомянуть_всех="Запинговать @everyone (по умолчанию выключено)",
)
@app_commands.choices(цвет=[app_commands.Choice(name=k, value=k) for k in COLOR_CHOICES])
async def announce(
    interaction: discord.Interaction,
    канал: discord.TextChannel = None,
    изображение: discord.Attachment = None,
    цвет: app_commands.Choice[str] = None,
    упомянуть_всех: bool = False,
):
    if not isinstance(interaction.user, discord.Member) or not has_role(interaction.user, ANNOUNCE_ROLE_ID):
        await interaction.response.send_message("Эта команда доступна только администрации.", ephemeral=True)
        return

    target = канал or interaction.channel
    color = COLOR_CHOICES[цвет.value] if цвет is not None else discord.Color.blurple()
    await interaction.response.send_modal(AnnounceModal(target, изображение, color, упомянуть_всех))


@bot.tree.command(name="rule", description="Отправить пункт правил", guild=GUILD_OBJECT)
@app_commands.describe(канал="Куда отправить (по умолчанию — текущий канал)")
async def rule(interaction: discord.Interaction, канал: discord.TextChannel = None):
    if not isinstance(interaction.user, discord.Member) or not has_role(interaction.user, ANNOUNCE_ROLE_ID):
        await interaction.response.send_message("Эта команда доступна только администрации.", ephemeral=True)
        return

    target = канал or interaction.channel
    await interaction.response.send_modal(RuleModal(target))


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
