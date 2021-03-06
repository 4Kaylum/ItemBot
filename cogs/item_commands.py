import asyncio
import typing
import random
import collections
from datetime import datetime as dt, timedelta

import asyncpg
import discord
from discord.ext import commands
import voxelbotutils as utils


class ItemCommands(utils.Cog):

    def __init__(self, bot:utils.Bot):
        super().__init__(bot)
        self.last_command_run = collections.defaultdict(lambda: dt(2000, 1, 1))

    @staticmethod
    def get_reaction_add_check(ctx:utils.Context, message:discord.Message, valid_reactions:typing.List[str]):
        """
        Creates a lambda for use in the add_reaction check.
        """

        return lambda r, u: str(r.emoji) in valid_reactions and u.id == ctx.author.id and r.message.id == message.id

    @utils.command(aliases=['inv', 'i'])
    @commands.bot_has_permissions(embed_links=True)
    @commands.guild_only()
    async def inventory(self, ctx:utils.Context, user:discord.Member=None):
        """
        Checks the inventory of a user.of
        """

        # Get items for the user
        user = user or ctx.author
        async with self.bot.database() as db:
            items = await db("SELECT item_name, amount FROM user_inventories WHERE guild_id=$1 AND user_id=$2", ctx.guild.id, user.id)
        if not items:
            return await ctx.send(f"**{user!s}** has no items :c")

        # Make embed
        with utils.Embed() as embed:
            embed.description = '\n'.join([
                f"{i['amount']:,}x {i['item_name']}" for i in items if i['amount'] > 0
            ])
            embed.set_author_to_user(user)
        return await ctx.send(embed=embed)

    @utils.command(aliases=['craft'])
    @commands.guild_only()
    async def craftitem(self, ctx:utils.Context, *, crafted_item_name:commands.clean_content):
        """
        Crafts a new item from your current inventory.
        """

        # See if there's a crafting recipe set up
        crafted_item_name = crafted_item_name.lower()
        async with self.bot.database() as db:
            item_craft_amount = await db("SELECT * FROM craftable_items WHERE guild_id=$1 AND item_name=$2", ctx.guild.id, crafted_item_name)
            if not item_craft_amount:
                return await ctx.send(f"You can't acquire **{crafted_item_name}** items via the crafting.")
            item_craft_ingredients = await db("SELECT * FROM craftable_item_ingredients WHERE guild_id=$1 AND item_name=$2", ctx.guild.id, crafted_item_name)
            user_inventory = await db("SELECT * FROM user_inventories WHERE guild_id=$1 AND user_id=$2", ctx.guild.id, ctx.author.id)

        # Add in some dictionaries to make this a lil easier
        ingredients = {i['ingredient_name']: i['amount'] for i in item_craft_ingredients}
        inventory_original = {i['item_name']: i['amount'] for i in user_inventory if i['item_name'] in ingredients}
        inventory = inventory_original.copy()

        # See if they have enough of the items
        max_craftable_amount = []
        for ingredient, required_amount in ingredients.items():
            if inventory.get(ingredient, 0) - required_amount < 0:
                return await ctx.send(f"You don't have enough **{ingredient}** items to craft this.")
            max_craftable_amount.append(inventory.get(ingredient) // required_amount)
        max_craftable_amount = min(max_craftable_amount)

        # Make sure they wanna make it
        ingredient_string = [f"`{o}x {i}`" for i, o in ingredients.items()]
        await ctx.send(f"This craft gives you **{item_craft_amount[0]['amount_created']}x {crafted_item_name}** and is made from {', '.join(ingredient_string)}. You can make this between 0 and {max_craftable_amount} times - how many times would you like to craft this?")
        try:
            crafting_amount_message = await self.bot.wait_for(
                "message", timeout=120.0,
                check=lambda m: m.channel.id == ctx.channel.id and m.author.id == ctx.author.id and m.content
            )
        except asyncio.TimeoutError:
            return await ctx.send("Timed out on crafting confirmation - please try again later.")

        # Get how many they want to craft, and make sure they can do it
        try:
            user_craft_amount = int(crafting_amount_message.content)
        except ValueError:
            their_value = await commands.clean_content().convert(ctx, crafting_amount_message.content)
            return await ctx.send(f"I couldn't convert `{their_value}` into an integer - please try again later.")

        # See if they said 0
        if user_craft_amount <= 0:
            return await ctx.send("Alright, aborting crafting!")

        # Remove the right amounts from their inventory
        for ingredient, required_amount in ingredients.items():
            if inventory[ingredient] - (required_amount * user_craft_amount) < 0:
                return await ctx.send(f"You don't have enough **{ingredient}** items to craft this.")
            inventory[ingredient] -= (required_amount * user_craft_amount)

        # Alter their inventory babey lets GO
        async with ctx.typing():
            async with self.bot.database() as db:
                for item, amount in inventory.items():
                    await db(
                        "UPDATE user_inventories SET amount=$4 WHERE guild_id=$1 AND user_id=$2 AND item_name=$3",
                        ctx.guild.id, ctx.author.id, item, amount
                    )
                await db(
                    """INSERT INTO user_inventories (guild_id, user_id, item_name, amount)
                    VALUES ($1, $2, $3, $4) ON CONFLICT (guild_id, user_id, item_name)
                    DO UPDATE SET amount=user_inventories.amount+excluded.amount""",
                    ctx.guild.id, ctx.author.id, crafted_item_name, item_craft_amount[0]['amount_created'] * user_craft_amount
                )
        return await ctx.send(f"You've sucessfully crafted **{item_craft_amount[0]['amount_created'] * user_craft_amount:,}x {crafted_item_name}**.")

    @utils.command()
    @commands.guild_only()
    async def getitem(self, ctx:utils.Context, *, item_name:commands.clean_content):
        """
        Gets you an item from the server.
        """

        # Get the item from the db
        item_name = item_name.lower()
        db = await self.bot.database.get_connection()
        acquire_information = await db(
            "SELECT * FROM guild_item_acquire_methods WHERE guild_id=$1 AND item_name=$2 AND acquired_by='Command'",
            ctx.guild.id, item_name
        )
        if not acquire_information:
            await db.disconnect()
            return await ctx.send(f"You can't acquire **{item_name}** items via the `getitem` command.")
        acquire_information = acquire_information[0]

        # See if they hit the timeout
        last_run = self.last_command_run[(ctx.guild.id, ctx.author.id, item_name)]
        if last_run + timedelta(seconds=acquire_information['acquire_per']) > dt.utcnow():
            cooldown_seconds = ((last_run + timedelta(seconds=acquire_information['acquire_per'])) - dt.utcnow()).total_seconds()
            cooldown_timevalue = utils.TimeValue(cooldown_seconds)
            await db.disconnect()
            return await ctx.send(f"You can't run this command again for another `{cooldown_timevalue.clean_spaced}`.")
        self.last_command_run[(ctx.guild.id, ctx.author.id, item_name)] = dt.utcnow()

        # Add to database
        amount = random.randint(acquire_information['min_acquired'], acquire_information['max_acquired'])
        await db(
            """INSERT INTO user_inventories (guild_id, user_id, item_name, amount)
            VALUES ($1, $2, $3, $4) ON CONFLICT (guild_id, user_id, item_name)
            DO UPDATE SET amount=user_inventories.amount+excluded.amount""",
            ctx.guild.id, ctx.author.id, item_name, amount,
        )
        await db.disconnect()
        return await ctx.send(f"You've received `{amount:,}x {item_name}`.")

    @utils.command(ignore_extra=False, aliases=['makeitem', 'additem'])
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def createitem(self, ctx:utils.Context, *, item_name:commands.clean_content):
        """
        Creates an item that people are able to get.
        """

        item_name = item_name.lower()
        async with self.bot.database() as db:
            try:
                await db("INSERT INTO guild_items (guild_id, item_name) VALUES ($1, $2)", ctx.guild.id, item_name)
            except asyncpg.UniqueViolationError:
                return await ctx.send(f"There's already an item with the name **{item_name}** in your guild.")
        return await ctx.send(f"Added an item with name **{item_name}** to your guild. Add acquire methods with the `{ctx.clean_prefix}getitem {item_name}` command.")

    @utils.command()
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(add_reactions=True, send_messages=True)
    @commands.guild_only()
    async def acquireitem(self, ctx:utils.Context, *, item_name:commands.clean_content):
        """
        Sets up how an item on your server can be acquired.
        """

        # Make sure the item exists
        item_name = item_name.lower()
        async with self.bot.database() as db:
            rows = await db("SELECT * FROM guild_items WHERE guild_id=$1 AND item_name=$2", ctx.guild.id, item_name)
        if not rows:
            return await ctx.send(f"There's no item with the name **{item_name}** in your guild. If you want one, you can set one up with `{ctx.clean_prefix}createitem {item_name}`.")

        # Send initial message
        self.logger.info(f"Setting up an item acquire for '{item_name}' in {ctx.guild.id}")
        command_message = await ctx.send("You can set up items to be acquired via messages sent (like level up exp, \N{BLUE HEART}), via command (like a daily command \N{GREEN HEART}), and/or via crafting (\N{YELLOW HEART}). What would you like to set up now?")
        valid_reactions = ["\N{BLUE HEART}", "\N{GREEN HEART}", "\N{YELLOW HEART}", "\N{HEAVY MULTIPLICATION X}"]
        for e in valid_reactions:
            await command_message.add_reaction(e)

        # Wait for a result
        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=120.0,
                check=self.get_reaction_add_check(ctx, command_message, valid_reactions),
                # check=lambda r, u: str(r.emoji) in valid_reactions and u.id == ctx.author.id and r.message.id == ctx.message.id
            )
            emoji = str(reaction.emoji)
        except asyncio.TimeoutError:
            self.logger.info(f"Timed out setting up item acquire for '{item_name}' in {ctx.guild.id}")
            return await ctx.send("Timed out setting up an item acquirement method - please try again later.")

        # They wanna abort
        if emoji == "\N{HEAVY MULTIPLICATION X}":
            self.logger.info(f"Aborted setting up item acquire for '{item_name}' in {ctx.guild.id}")
            return await ctx.send(f"Alright, aborting setting up an item acquire method for '{item_name}'.")

        # They wanna set up a command
        elif emoji == "\N{GREEN HEART}":
            self.logger.info(f"Setting up a command acquire for '{item_name}' in {ctx.guild.id}")
            return await self.set_up_acquire_command(ctx, item_name)

        # They wanna set up messasge acquire
        elif emoji == "\N{BLUE HEART}":
            self.logger.info(f"Setting up a message acquire for '{item_name}' in {ctx.guild.id}")
            return await self.set_up_message_acquire(ctx, item_name)

        # They wanna set up messasge acquire
        elif emoji == "\N{YELLOW HEART}":
            self.logger.info(f"Setting up a crafting recipe for '{item_name}' in {ctx.guild.id}")
            return await self.set_up_crafting_recipe(ctx, item_name)

        # And they managed to get something else
        self.logger.info(f"Fucked up setting up item acquire for '{item_name}' in {ctx.guild.id} - managed to pass with a {emoji} emoji")
        return await ctx.send("I don't think you should ever see this message.")

    async def set_up_acquire_command(self, ctx:utils.Context, item_name:str):
        """
        Talks the user through setting up a command where the user an acquire an item.
        """

        # See if stuff's already been set up
        async with self.bot.database() as db:
            rows = await db("SELECT * FROM guild_item_acquire_methods WHERE guild_id=$1 AND item_name=$2 AND acquired_by='Command'", ctx.guild.id, item_name)

        if rows:
            # See if they want to remove their current setup
            valid_reactions = ["\N{HEAVY MULTIPLICATION X}", "\N{BLACK QUESTION MARK ORNAMENT}"]
            acquire_method_setup = await ctx.send(f"You already have an acquire method set up for commands via the `{ctx.clean_prefix}getitem {item_name}` command. Would you like to remove this command (\N{HEAVY MULTIPLICATION X}) or change how the command works (\N{BLACK QUESTION MARK ORNAMENT})?")
            for e in valid_reactions:
                await acquire_method_setup.add_reaction(e)
            try:
                reaction, _ = await self.bot.wait_for(
                    "reaction_add", timeout=120.0,
                    check=self.get_reaction_add_check(ctx, acquire_method_setup, valid_reactions)
                )
                emoji = str(reaction.emoji)
            except asyncio.TimeoutError:
                return await ctx.send("Timed out setting up an item acquirement via command - please try again later.")

            # See if they just wanna delete
            if emoji == "\N{HEAVY MULTIPLICATION X}":
                async with self.bot.database() as db:
                    await db("DELETE FROM guild_item_acquire_methods WHERE guild_id=$1 AND item_name=$2 AND acquired_by='Command'", ctx.guild.id, item_name)
                return await ctx.send(f"Deleted the `{ctx.clean_prefix}getitem {item_name}` command.")

        # See their random amount minimum
        await ctx.send(f"When the `{ctx.clean_prefix}getitem {item_name}` command is run, they'll be given a random amount of the item - what's the _minimum_ you want users to be able to get?")
        try:
            user_message_minimum = await self.bot.wait_for(
                "message", timeout=120.0,
                check=lambda m: m.channel.id == ctx.channel.id and m.author.id == ctx.author.id and m.content
            )
            if not user_message_minimum.content.isdigit():
                their_value = await commands.clean_content().convert(ctx, user_message_minimum.content)
                return await ctx.send(f"I couldn't convert `{their_value}` into an integer - please try again later.")
        except asyncio.TimeoutError:
            return await ctx.send("Timed out setting up an item acquirement via command - please try again later.")

        # See their random amount maximum
        await ctx.send("What's the _maximum_ you want users to be able to get?")
        try:
            user_message_maximum = await self.bot.wait_for(
                "message", timeout=120.0,
                check=lambda m: m.channel.id == ctx.channel.id and m.author.id == ctx.author.id and m.content
            )
            if not user_message_maximum.content.isdigit():
                their_value = await commands.clean_content().convert(ctx, user_message_maximum.content)
                return await ctx.send(f"I couldn't convert `{their_value}` into an integer - please try again later.")
        except asyncio.TimeoutError:
            return await ctx.send("Timed out setting up an item acquirement via command - please try again later.")

        # See how often the command should be able to be run
        await ctx.send("Obviously the command shouldn't be run all the time - how often should users be able to run the command (eg `1h`, `5m`, etc)?")
        try:
            user_message_timeout = await self.bot.wait_for(
                "message", timeout=120.0,
                check=lambda m: m.channel.id == ctx.channel.id and m.author.id == ctx.author.id and m.content
            )
            try:
                timeout_timevalue = await utils.TimeValue.convert(ctx, user_message_timeout.content)
            except commands.BadArgument:
                their_value = await commands.clean_content().convert(ctx, timeout_timevalue.content)
                return await ctx.send(f"I couldn't convert `{their_value}` into a time value - please try again later.")
        except asyncio.TimeoutError:
            return await ctx.send("Timed out setting up an item acquirement via command - please try again later.")

        # Validate our information
        command_random_amounts = [int(user_message_maximum.content), int(user_message_minimum.content)]
        random_max = max(command_random_amounts)
        random_min = min(command_random_amounts)
        # timeout_timevalue is also here

        # Save the information to database
        async with self.bot.database() as db:
            await db(
                """INSERT INTO guild_item_acquire_methods (guild_id, item_name, acquired_by, min_acquired,
                max_acquired, acquire_per) VALUES ($1, $2, 'Command', $3, $4, $5) ON CONFLICT (guild_id, item_name, acquired_by) DO UPDATE
                SET min_acquired=$3, max_acquired=$4, acquire_per=$5""",
                ctx.guild.id, item_name, random_min, random_max, timeout_timevalue.delta.total_seconds()
            )
        return await ctx.send(f"Information saved to database - you can now acquire between `{random_min:,}` and `{random_max:,}` of **{item_name}** every `{timeout_timevalue.clean_spaced}` via the `{ctx.clean_prefix}getitem {item_name}` command.")

    async def set_up_message_acquire(self, ctx:utils.Context, item_name:str):
        """
        Talks the user through setting up item acquires via message sends.
        """

        return await ctx.send("I didn't actually code this yet so whoops")

    async def set_up_crafting_recipe(self, ctx:utils.Context, item_name:str):
        """
        Talks the user through setting up an item crafting recipe.
        """

        # See if stuff's already been set up
        async with self.bot.database() as db:
            rows = await db("SELECT * FROM craftable_items WHERE guild_id=$1 AND item_name=$2", ctx.guild.id, item_name)

        if rows:
            # See if they want to remove their current setup
            valid_reactions = ["\N{HEAVY MULTIPLICATION X}", "\N{BLACK QUESTION MARK ORNAMENT}"]
            acquire_method_setup = await ctx.send(f"You already have an acquire method set up for crafting via the `{ctx.clean_prefix}craftitem {item_name}` command. Would you like to remove this (\N{HEAVY MULTIPLICATION X}) or change how the crafting works (\N{BLACK QUESTION MARK ORNAMENT})?")
            for e in valid_reactions:
                await acquire_method_setup.add_reaction(e)
            try:
                reaction, _ = await self.bot.wait_for(
                    "reaction_add", timeout=120.0,
                    check=self.get_reaction_add_check(ctx, acquire_method_setup, valid_reactions)
                )
                emoji = str(reaction.emoji)
            except asyncio.TimeoutError:
                return await ctx.send("Timed out setting up an item acquirement via crafting - please try again later.")

            # See if they just wanna delete
            if emoji == "\N{HEAVY MULTIPLICATION X}":
                async with self.bot.database() as db:
                    await db("DELETE FROM craftable_items WHERE guild_id=$1 AND item_name=$2", ctx.guild.id, item_name)
                    await db("DELETE FROM craftable_item_ingredients WHERE guild_id=$1 AND item_name=$2", ctx.guild.id, item_name)
                return await ctx.send(f"Deleted the crafting recipe for `{item_name}` items.")
            async with self.bot.database() as db:
                await db("DELETE FROM craftable_items WHERE guild_id=$1 AND item_name=$2", ctx.guild.id, item_name)
                await db("DELETE FROM craftable_item_ingredients WHERE guild_id=$1 AND item_name=$2", ctx.guild.id, item_name)

        ingredient_list = []

        # Ask what the item will be made from initially
        ingredient_bot_message = await ctx.send("What item, and how many of that item, make up an ingredient of this crafting recipe (eg `5 cat`, `1 pizza slice`, `69 bee`, etc)?\n(Items are not checked until the end, so make sure you're spelling things correctly)")
        try:
            ingredient_user_message = await self.bot.wait_for(
                "message", timeout=120.0,
                check=lambda m: m.author.id == ctx.author.id and m.channel.id == ctx.channel.id and m.content,
            )
        except asyncio.TimeoutError:
            return await ctx.send("Timed out setting up an item acquirement via crafting - please try again later.")

        # Parse ingredient
        amount_str, *ingredient_name = ingredient_user_message.content.split(' ')
        if not amount_str.isdigit():
            their_value = await commands.clean_content().convert(ctx, amount_str)
            return await ctx.send(f"I couldn't convert `{their_value}` into an integer - please try again later.")
        ingredient_list.append((int(amount_str), ' '.join(ingredient_name)))

        # Ask about the rest of the ingredients
        while True:
            ingredient_bot_message = await ctx.send("Is there another item that's part of this recipe (eg `5 cat`, `1 pizza slice`, `69 bee`, etc)? If not, just react (\N{HEAVY MULTIPLICATION X}) below.")
            await ingredient_bot_message.add_reaction("\N{HEAVY MULTIPLICATION X}")
            try:
                done, pending = await asyncio.wait([
                    self.bot.wait_for('message', check=lambda m: m.author.id == ctx.author.id and m.channel.id == ctx.channel.id and m.content),
                    self.bot.wait_for('reaction_add', check=self.get_reaction_add_check(ctx, ingredient_bot_message, ["\N{HEAVY MULTIPLICATION X}"])),
                ], return_when=asyncio.FIRST_COMPLETED, timeout=120.0)
                for future in pending:
                    future.cancel()  # we don't need these anymore
            except asyncio.TimeoutError:
                return await ctx.send("Timed out setting up an item acquirement via crafting - please try again later.")

            # Did they message or react?
            result = done.pop().result()
            if isinstance(result, discord.Message):
                ingredient_user_message = result
            else:
                break

            # Parse ingredient
            amount_str, *ingredient_name = ingredient_user_message.content.split(' ')
            if not amount_str.isdigit():
                their_value = await commands.clean_content().convert(ctx, amount_str)
                return await ctx.send(f"I couldn't convert `{their_value}` into an integer - please try again later.")
            ingredient_list.append((int(amount_str), ' '.join(ingredient_name)))

        # Ask how many of the item should be created
        await ctx.send(f"How many `{item_name}` should be created from this crafting recipe?")
        try:
            item_create_amount_message = await self.bot.wait_for(
                "message", timeout=120.0,
                check=lambda m: m.author.id == ctx.author.id and m.channel.id == ctx.channel.id and m.content
            )
        except asyncio.TimeoutError:
            return await ctx.send("Timed out setting up an item acquirement via crafting - please try again later.")
        try:
            item_create_amount = int(item_create_amount_message.content)
        except ValueError:
            their_value = await commands.clean_content().convert(ctx, item_create_amount_message.content)
            return await ctx.send(f"I couldn't convert `{their_value}` into an integer - please try again later.")

        # Check that all the given items exist
        db = await self.bot.database.get_connection()
        all_items = await db("SELECT * FROM guild_items WHERE guild_id=$1", ctx.guild.id)
        invalid_items = [i for i in ingredient_list if i[1] not in [o['item_name'] for o in all_items]]
        if invalid_items:
            await db.disconnect()
            return await ctx.send(f"You gave some invalid items in your ingredients - {invalid_items!s} - please try again later.")

        # Add them to the database
        async with ctx.typing():
            await db(
                """INSERT INTO craftable_items (guild_id, item_name, amount_created)
                VALUES ($1, $2, $3)""",
                ctx.guild.id, item_name, item_create_amount
            )
            for amount, ingredient_name in ingredient_list:
                await db(
                    """INSERT INTO craftable_item_ingredients (guild_id, item_name, ingredient_name, amount)
                    VALUES ($1, $2, $3, $4)""",
                    ctx.guild.id, item_name, ingredient_name, amount
                )

        # And respond
        await db.disconnect()
        return await ctx.send("Your crafting recipe has been added!")

    @commands.command()
    @commands.guild_only()
    async def itemmap(self, ctx:utils.Context):
        """Shows the item map for your guild"""

        # Get the items for the guild
        async with self.bot.database() as db:
            acquirable_items = await db("SELECT * FROM guild_item_acquire_methods WHERE guild_id=$1", ctx.guild.id)
            craftable_items = await db("SELECT * FROM craftable_item_ingredients WHERE guild_id=$1", ctx.guild.id)

        # Let's start off our dot right
        lines = [
            'digraph{',
            'overlap=scale;',
            'node[style=filled];',
            'bgcolour=transparent;',
            'command[label="getitem", fillcolor=lightblue];',
        ]
        start_time = dt.utcnow()

        # Go through each item
        for item in acquirable_items:
            if item['acquired_by'] == 'Command':
                lines.append(f'command -> "{item["item_name"]}" [label="{item["min_acquired"]}-{item["max_acquired"]}x"];')
        for item in craftable_items:
            lines.append(f'"{item["ingredient_name"]}" -> "{item["item_name"]}" [label="{item["amount"]}x"];')
        lines.append('}')
        all_code = ''.join(lines)

        # Write code to file
        try:
            with open(f'{ctx.guild.id}.gz', 'w', encoding='utf-8') as a:
                a.write(all_code)
        except Exception as e:
            self.logger.error(f"Could not write to {ctx.guild.id}.gz")
            raise e

        # Convert to an image
        dot = await asyncio.create_subprocess_exec(*[
            'neato',
            '-Tpng',
            f'{ctx.guild.id}.gz',
            '-o',
            f'{ctx.guild.id}.png',
            '-Gcharset=UTF-8',
        ], loop=self.bot.loop)
        await asyncio.wait_for(dot.wait(), 10.0, loop=self.bot.loop)

        # Kill subprocess
        try:
            dot.kill()
        except ProcessLookupError:
            pass  # It already died
        except Exception as e:
            raise e

        # Get time taken
        end_time = dt.now()
        time_taken = (end_time - start_time).total_seconds()

        # Send file
        file = discord.File(fp=f'{ctx.guild.id}.png')
        text = f"Generated in `{time_taken:.2f}` seconds from `{len(all_code)}` bytes of DOT code, "
        await ctx.send(text, file=file)

        # Delete cached
        self.bot.loop.create_task(asyncio.create_subprocess_exec('rm', f'{ctx.guild.id}.gz', loop=self.bot.loop))
        self.bot.loop.create_task(asyncio.create_subprocess_exec('rm', f'{ctx.guild.id}.png', loop=self.bot.loop))


def setup(bot:utils.Bot):
    x = ItemCommands(bot)
    bot.add_cog(x)
