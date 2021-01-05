import discord
import os

from discord.ext import commands
from dotenv import load_dotenv

# test
load_dotenv()

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix='$', intents=intents)

@bot.event
async def on_message(message):

    if message.author.id == bot.user.id:
        return

    if message.content.lower() == 'saludo':
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

    await bot.process_commands(message)


@bot.command(pass_context=True)
async def say(ctx, *args):
    user_args = ' '.join(args)

    await ctx.channel.send(user_args)

@bot.event
async def on_ready():
    print('Logged in as {0} ({0.id})'.format(bot.user))
    print('---------------------------------------------')

bot.run(os.getenv('TOKEN'))
