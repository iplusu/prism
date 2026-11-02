import pickle

def load_cached_result(blob):
    return pickle.loads(blob)
