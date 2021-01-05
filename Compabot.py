import discord
import os

from discord.ext import commands
from discord.utils import get
from discord import FFmpegPCMAudio
from youtube_dl import YoutubeDL
from dotenv import load_dotenv

# test 2
load_dotenv()

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix='$', intents=intents)
client = discord.Client()

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
    elif message.content.lower().startswith('what happened to regino'):
        await message.channel.send('Valio verga...')

    await bot.process_commands(message)


@bot.command(pass_context=True)
async def say(ctx, *args):
    user_args = ' '.join(args)

    await ctx.channel.send(user_args)

@bot.command(brief="Plays a single video, from a youtube URL")
async def rickroll(ctx):
    URL = 'https://www.youtube.com/watch?v=oHg5SJYRHA0'
    YDL_OPTIONS = {'format': 'bestaudio', 'noplaylist':'True'}
    FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
    voice = get(client.voice_clients, guild=ctx.guild)

    if not voice.is_playing():
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_link, download=False)
        # URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS))
        voice.is_playing()
    else:
        await ctx.send("Already playing song")
        return

@bot.event
async def on_ready():
    print('Logged in as {0} ({0.id})'.format(bot.user))
    print('---------------------------------------------')

bot.run(os.getenv('TOKEN'))
