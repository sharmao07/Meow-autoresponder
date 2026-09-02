import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from dotenv import load_dotenv
import math
from pymongo import MongoClient
import keep_alive

# 1. Load the secret token
load_dotenv()
TOKEN = os.getenv('RESPONDER_TOKEN')

# 2. Setup Intents & Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="?meow ", intents=intents)

# 🔒 CONFIGURATION
ALLOWED_CATEGORY_ID = 1402027749350314075 
ALLOWED_ROLE_IDS = [1491116707199193158]

# 📁 Cloud Database Helpers (MongoDB)
MONGO_URI = os.getenv('MONGO_URI')
cluster = MongoClient(MONGO_URI)
db = cluster["meowresponder"]
collection = db["autoresponses"]

def load_responses():
    try:
        docs = collection.find({})
        data = {}
        for doc in docs:
            data[doc["_id"]] = doc["responses"]
        return data
    except Exception as e:
        print(f"Error reading from MongoDB: {e}")
        return {}

def save_responses(data):
    try:
        for guild_id, responses in data.items():
            collection.update_one(
                {"_id": guild_id},
                {"$set": {"responses": responses}},
                upsert=True
            )
            
        # Clean up any guilds that deleted all their triggers
        existing_guilds = [doc["_id"] for doc in collection.find({})]
        for g_id in existing_guilds:
            if g_id not in data:
                collection.delete_one({"_id": g_id})
    except Exception as e:
        print(f"Error saving to MongoDB: {e}")

# 🛡️ Security Check
def is_slash_authorized(interaction: discord.Interaction) -> bool:
    # 1. Anyone with Server Administrator permissions bypasses all restrictions
    if interaction.user.guild_permissions.administrator:
        return True

    # 2. For non-admins: check if in a mod channel or allowed category
    channel_name = getattr(interaction.channel, "name", "").lower()
    is_mod_channel_name = "mod" in channel_name
    is_in_allowed_category = getattr(interaction.channel, "category_id", None) == ALLOWED_CATEGORY_ID
    
    if not (is_mod_channel_name or is_in_allowed_category):
        return False
    
    # 3. Check if non-admin has the allowed staff role
    user_role_ids = [role.id for role in interaction.user.roles]
    return any(allowed_id in user_role_ids for allowed_id in ALLOWED_ROLE_IDS))

@bot.event
async def on_ready():
    print("-----------------------------------------------")
    print(f"Logged in successfully as: {bot.user.name}")
    print("Clean 4-Command System: READY")
    print("-----------------------------------------------")

# 🔄 Maintenance Sync Command
@bot.command(name="sync")
@commands.has_permissions(administrator=True)
async def sync_slash_commands(ctx):
    await ctx.send("🔄 Syncing commands to Discord...")
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ Successfully synced {len(synced)} slash commands!")
    except Exception as e:
        await ctx.send(f"❌ Failed to sync: {e}")

@bot.command(name="clean")
@commands.has_permissions(administrator=True)
async def clean_server_commands(ctx):
    await ctx.send("🧹 Wiping old server-specific ghost commands...")
    try:
        # This empties the command list for your specific server
        bot.tree.clear_commands(guild=ctx.guild)
        await bot.tree.sync(guild=ctx.guild)
        await ctx.send("✅ Old commands deleted! You should only see the clean global list now.")
    except Exception as e:
        await ctx.send(f"❌ Failed to clean: {e}")        

# 1️⃣ SLASH COMMAND: /add
@bot.tree.command(name="add", description="Add a new autoresponder trigger")
@app_commands.describe(
    trigger="The word to look out for", 
    emoji="The emoji to react with",
    case_sensitive="True = Exact word match only (any casing) | False = Match anywhere in a sentence"
)
async def add_responder(interaction: discord.Interaction, trigger: str, emoji: str, case_sensitive: bool):
    if not is_slash_authorized(interaction):
        return await interaction.response.send_message("❌ This action is restricted to staff.", ephemeral=True)
        
    await interaction.response.defer()
    responses = load_responses()
    guild_id = str(interaction.guild.id)
    if guild_id not in responses:
        responses[guild_id] = {}
        
    word_key = trigger.lower()
    responses[guild_id][word_key] = {
        "emoji": emoji,
        "exact": case_sensitive
    }
    save_responses(responses)
    
    match_lbl = "Exact Word Only" if case_sensitive else "Anywhere in Sentence"
    await interaction.followup.send(f"✅ **Added!** [{match_lbl}]\nTrigger: **{word_key}** → {emoji}")
    
# 2️⃣ SLASH COMMAND: /remove
@bot.tree.command(name="remove", description="Remove an autoresponder trigger")
@app_commands.describe(trigger="The word trigger you want to delete")
async def remove_responder(interaction: discord.Interaction, trigger: str):
    if not is_slash_authorized(interaction):
        return await interaction.response.send_message("❌ This action is restricted to staff.", ephemeral=True)
        
    await interaction.response.defer()
    responses = load_responses()
    guild_id = str(interaction.guild.id)
    word_key = trigger.lower()
    
    if guild_id in responses and word_key in responses[guild_id]:
        del responses[guild_id][word_key]
        save_responses(responses)
        return await interaction.followup.send(f"🗑️ Successfully removed trigger: **{word_key}**")
        
# 3️⃣ SLASH COMMAND: /edit
@bot.tree.command(name="edit", description="Edit an existing autoresponder trigger")
@app_commands.describe(
    trigger="The existing word trigger to edit", 
    emoji="The new emoji to react with",
    case_sensitive="True = Exact word match only (any casing) | False = Match anywhere in a sentence"
)
async def edit_responder(interaction: discord.Interaction, trigger: str, emoji: str, case_sensitive: bool):
    if not is_slash_authorized(interaction):
        return await interaction.response.send_message("❌ This action is restricted to staff.", ephemeral=True)
        
    await interaction.response.defer()
    responses = load_responses()
    guild_id = str(interaction.guild.id)
    word_key = trigger.lower()
    
    if guild_id not in responses or word_key not in responses[guild_id]:
        return await interaction.followup.send(f"❌ '{trigger}' does not exist. Use `/add` to create it first.")
        
    responses[guild_id][word_key] = {
        "emoji": emoji,
        "exact": case_sensitive
    }
    save_responses(responses)
    
    match_lbl = "Exact Word Only" if case_sensitive else "Anywhere in Sentence"
    await interaction.followup.send(f"📝 **Updated!** [{match_lbl}]\nTrigger: **{word_key}** → {emoji}")
    
# --- PAGINATION SYSTEM ---
class PaginationView(discord.ui.View):
    def __init__(self, data, author_name, author_icon):
        super().__init__(timeout=180) # Buttons time out after 3 minutes
        self.data = data
        self.author_name = author_name
        self.author_icon = author_icon
        self.current_page = 1
        self.per_page = 10
        self.total_pages = math.ceil(len(self.data) / self.per_page) if self.data else 1
        
        # Disable "Prev" button on page 1
        self.children[0].disabled = True 
        # Disable "Next" button if there is only 1 page total
        if self.total_pages <= 1:
            self.children[1].disabled = True

    def format_page(self):
        start = (self.current_page - 1) * self.per_page
        end = start + self.per_page
        page_items = self.data[start:end]
        
        description = "🌟 **Autoresponders Setup**\n\n" + "\n".join(page_items)
        
        embed = discord.Embed(description=description, color=discord.Color.from_str("#72bcd4"))
        if self.author_icon:
            embed.set_author(name=self.author_name, icon_url=self.author_icon)
        else:
            embed.set_author(name=self.author_name)
            
        embed.set_footer(text=f"Page {self.current_page} of {self.total_pages} • Total Triggers: {len(self.data)}")
        return embed

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        # Update button states
        self.children[0].disabled = self.current_page == 1
        self.children[1].disabled = False
        await interaction.response.edit_message(embed=self.format_page(), view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        # Update button states
        self.children[1].disabled = self.current_page == self.total_pages
        self.children[0].disabled = False
        await interaction.response.edit_message(embed=self.format_page(), view=self)


# 4️⃣ SLASH COMMAND: /list
@bot.tree.command(name="list", description="Show all active server autoresponders")
async def list_responders(interaction: discord.Interaction):
    if not is_slash_authorized(interaction):
        return await interaction.response.send_message("❌ This command is restricted.", ephemeral=True)
        
    await interaction.response.defer()
    responses = load_responses()
    guild_id = str(interaction.guild.id)
    server_responses = responses.get(guild_id, {})
    
    list_lines = []
    for word, data in server_responses.items():
        if isinstance(data, dict):
            is_exact = data.get("exact", False)
            emoji = data.get("emoji", "❓")
            match_lbl = "Exact Word" if is_exact else "Broad Match"
            list_lines.append(f"• **{word}** → {emoji} ({match_lbl})")
        else:
            list_lines.append(f"• **{word}** → {data} (Broad Match)")
            
    list_lines.sort()
    
    if not list_lines:
        embed = discord.Embed(description="🌟 **Autoresponders Setup**\n\nNo autoresponders setup in this server.", color=discord.Color.from_str("#72bcd4"))
        if interaction.guild:
            icon_url = interaction.guild.icon.url if interaction.guild.icon else None
            embed.set_author(name=interaction.guild.name, icon_url=icon_url)
        return await interaction.followup.send(embed=embed)

    author_name = interaction.guild.name if interaction.guild else "Autoresponders"
    author_icon = interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None

    view = PaginationView(list_lines, author_name, author_icon)
    
    await interaction.followup.send(embed=view.format_page(), view=view)


# 📥 Background Message Scanner
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    responses = load_responses()
    guild_id = str(message.guild.id)
    server_responses = responses.get(guild_id, {})
    
    # Force clean lowercase message processing to handle HuGa/HUGA/huga identically
    content_clean = message.content.strip().lower()

    for word, data in server_responses.items():
        emoji_to_use = data.get("emoji") if isinstance(data, dict) else data
        is_exact = data.get("exact", False) if isinstance(data, dict) else False

        trigger_matched = False
        if is_exact:
            # Must match the standalone word completely (ignoring capitalization)
            if content_clean == word:
                trigger_matched = True
        else:
            # Broad match anywhere inside the sentence (ignoring capitalization)
            if word in content_clean:
                trigger_matched = True

        if trigger_matched:
            try:
                if emoji_to_use.startswith("<") and emoji_to_use.endswith(">"):
                    clean_emoji = emoji_to_use.replace("<:", "").replace("<a:", "").replace(">", "")
                    name, emoji_id = clean_emoji.split(":")
                    emoji_obj = bot.get_emoji(int(emoji_id))
                    await message.add_reaction(emoji_obj if emoji_obj else emoji_to_use)
                else:
                    await message.add_reaction(emoji_to_use)
            except Exception as e:
                print(f"Failed to add reaction: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    keep_alive.keep_alive()
    bot.run(TOKEN)
