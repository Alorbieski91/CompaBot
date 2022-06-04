import discord
import os

from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"),
                   description='Compabot: Your best friend that can do a lot of things.',
                   intents=intents)

@bot.event
async def on_message(message):

    if message.author.id == bot.user.id:
        return

    if message.content.lower() == 'saludos' or message.content.lower() == 'hello':
        await message.channel.send('Saludos compa!')
    elif message.content.lower() == 'bye':
        await message.channel.send('Hay nos vemos compa.')

    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    channel = member.guild.system_channel
    if channel is not None:
        await channel.send('Well, well, well... {0.mention} decided to show up...'.format(member))

@bot.command(pass_context=True)
async def say(ctx, *, arg):

    await ctx.channel.send(arg)

@bot.event
async def on_ready():
    print('Logged in as {0} ({0.id})'.format(bot.user))
    print('---------------------------------------------')

bot.run(os.getenv('TOKEN'))
