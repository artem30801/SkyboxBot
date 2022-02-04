import asyncio


async def test_dissnek(loop, token: str):
    import dis_snek

    client = dis_snek.Snake()
    print("Loop", loop)
    loop.create_task(client.login(token))
    print("Waiting for startup")
    await client.wait_for("startup")
    print("DONE")


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_dissnek(loop, "NTY5MDc1MzQ0OTM4ODkzMzIy.XLrWtw.RYhqLPflNzb-HAXbFqMEXNNFYsk"))


if __name__ == "__main__":
    main()
