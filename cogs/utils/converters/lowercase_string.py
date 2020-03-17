from discord.ext import commands


class StringLowercase(str):
    """Just a normal str but it's auto-converted to lowercase innit"""

    async def convert(self, ctx, value):
        return value.lower()


class CleanContentLowercase(str):
    """Normal str but it's lowercase and it inherits from clean content"""

    async def convert(self, ctx, value):
        value = await commands.clean_content().convert(ctx, value)
        return value.lower()
