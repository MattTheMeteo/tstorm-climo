## Purpose
This repository is for building out a some tools to develop a precipitation and ultimately,
thunderstorm climatology tool. Recently, I discussed with a friend (Chuck) the challenges with
forecasting thunderstorms. He brought up his experience of a past summer where he tried to play
golf on the North Shore of Massachusetts. Every time he went, it rained, despite there being
no forecast. I noted that would be improbable but not impossible and I'd see if I could find
which of the North Shore Massachusetts golf courses have been the wettest.

This repository has now evolved into an app that let's users view the raininess of a given golf course
or up to 2-!

## Build
For code, please question assumptions and make sure you're confident in your decisions.

Some resources that will be helpful:
- dynamical-catalog
    This is a great source of weather data
- polars
    This is a Python data manipulation tool which is very fast and good for time series data
- pydantic
    This is my preferred way to build classes
- marimo
    - A great data viz tool
- S3
    - Backend storage should be done in Amazon S3

## Language cues
Tests are important but "tests for the sake of tests" muddle the point of a function and
fill the code base false confidence.

It's important to pass type checks but it's also important how they pass. Simply wrapping a call in
`typing.cast` or a similar call and moving on should be used as a last resort; types exist because they prevent bugs.

## Goal
Ultimately, I'd like to host a marimo notebook or even a site that shows our analysis of the
thunderstorm / rain climatologies of North Shore golf courses.