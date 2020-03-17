from discord.ext import commands


class StringLowercase(str):
    """Just a normal str but it's auto-converted to lowercase innit"""

    @classmethod
    async def convert(cls, ctx, value):
        return value.lower()


class CleanContentLowercase(str):
    """Normal str but it's lowercase and it inherits from clean content"""

    @classmethod
    async def convert(cls, ctx, value):
        value = await commands.clean_content().convert(ctx, value)
        return value.lower()
