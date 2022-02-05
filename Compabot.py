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
        await message.channel.send('Saludos compa ALV!')
    elif message.content.lower() == 'bye':
        await message.channel.send('Hay nos vemos compa. Tallatelo')
    elif message.content.lower() == 'it is hot today':
        await message.channel.send('Eres joto guey?!')
    elif message.content.lower() == 'it is cold today':
        await message.channel.send('Eres frio guey?!')
    elif message.content.lower() == 'it is certain':
        await message.channel.send('es cierto guey!')
    elif message.content.lower() == 'marco':
        await message.channel.send('polo!')
    elif message.content.lower().startswith('what happened to regino'):
        await message.channel.send('Valio verga...')

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
