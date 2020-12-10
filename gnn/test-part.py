import pickle

proj = ""
with open('wa-part-proj.pkl', 'rb') as handle:
    proj = pickle.load(handle)


proj.run()
