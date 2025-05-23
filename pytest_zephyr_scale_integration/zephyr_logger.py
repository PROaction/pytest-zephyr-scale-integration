import logging

def setup_logger(log_file: str = 'zephyr_integration.log') -> logging.Logger:
    logger = logging.getLogger('zephyr_integration')
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding='utf-8')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    return logger
