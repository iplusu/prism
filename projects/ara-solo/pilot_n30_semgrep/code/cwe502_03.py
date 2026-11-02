import pickle

def import_widget_definition(widget_blob):
    return pickle.loads(widget_blob)
