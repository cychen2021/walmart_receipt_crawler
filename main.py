def main():
    # Delegate to the package CLI entry point
    from walmart_receipt_crawler.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
