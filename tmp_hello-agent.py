from pyspark.sql import SparkSession
spark = SparkSession.builder.appName("hello").getOrCreate()
df = spark.createDataFrame([(1, "a")], ["id", "val"])
df.show()
spark.stop()
