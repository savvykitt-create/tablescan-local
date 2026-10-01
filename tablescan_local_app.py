if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    from tablescan_local.main import main
    raise SystemExit(main())
