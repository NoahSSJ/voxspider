from core.huggingface import HuggingfaceSpider, SpiderConfig

datasets = SpiderConfig.get_datasets()
ds = datasets[0]
spider = HuggingfaceSpider(ds).get_manifest()
    