# Fire-and-Smoke-Detection-v1

The Fire and Smoke detection AI (F&Sv1) has been trained to detect as the name suggests, Fire and Smoke.
This is only the first version, and soon I will also make a repository of the second version which will be trained on a much larger dataset.
It also detects bright lights, labelling them as "other" to prevent it from confusing it with fires.
![App Demo](demo.gif)

## Locations
You can find the **weights** here: [weights](./weights/)

If you interested how the AI performed while testing, you can find the **predicted and actual images** here: [pred_vs_actual](./pred_vs_actual/)
Or, you can look at the graphs here: [graphs](./graphs/)

You can also check out a **video demo** of the AI at work here: [demo.mp4](./demo.mp4)

And if you are interesting in seeing a live demo, you can check out the python scripts at [demo_py_scripts](./demo_py_script/)

Do note, there are two **demos**, one which opens your camera, and **detects flames and smoke via your camera live**, and the second
which **detects fire and smoke in any video** you upload. You can see the comments inside the script to see more details.

## How to use this Project.

While I have uploaded python scripts using which you can test out the AI, the main aim of this project was to provide weights
**so they could be used according to the needs of your project**. Which is why I have provided the weights **best.pt** and **last.pt**. 

## Future versions.

This is the first version of these weights, and I plan on training this AI on a much larger dataset, whose repositories link I will put **HERE** when it is published on GitHub.

## Dataset.

The dataset was taken from **Roboflow**, which was uploded by a **Roboflow user.**

You can find the database [here](https://universe.roboflow.com/catargiuconstantin/firesmokedataset).

You can see the license [here](./dataset.txt)
